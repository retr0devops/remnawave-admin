"""Advanced Analytics API — geo map, top users, trends, node metrics history."""
import logging
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Query, Request

from web.backend.api.deps import require_permission, AdminUser
from web.backend.core.cache import cached, CACHE_TTL_LONG
from web.backend.core.rate_limit import limiter, RATE_ANALYTICS

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/geo")
@limiter.limit(RATE_ANALYTICS)
async def get_geo_connections(
    request: Request,
    period: str = Query("7d", description="Period: 24h, 7d, 30d"),
    date_from: Optional[str] = Query(None, description="Custom start date (ISO 8601)"),
    date_to: Optional[str] = Query(None, description="Custom end date (ISO 8601)"),
    admin: AdminUser = Depends(require_permission("analytics", "view")),
):
    """Get geographical distribution of user connections from violations/IP metadata."""
    return await _compute_geo(period=period, date_from=date_from, date_to=date_to)


@cached("analytics:geo", ttl=CACHE_TTL_LONG, key_args=("period", "date_from", "date_to"))
async def _compute_geo(period: str = "7d", date_from: Optional[str] = None, date_to: Optional[str] = None):
    """Compute geo connections (cacheable)."""
    try:
        from shared.database import db_service
        if not db_service.is_connected:
            return {"countries": [], "cities": []}

        now = datetime.now(timezone.utc)
        if date_from:
            since = datetime.fromisoformat(date_from.replace("Z", "+00:00"))
        else:
            delta_map = {"24h": 1, "7d": 7, "30d": 30}
            days = delta_map.get(period, 7)
            since = now - timedelta(days=days)

        async with db_service.acquire() as conn:
            # Get country distribution from ip_metadata table
            country_rows = await conn.fetch(
                """
                SELECT country_name, country_code, COUNT(*) as count
                FROM ip_metadata
                WHERE created_at >= $1 AND country_name IS NOT NULL
                GROUP BY country_name, country_code
                ORDER BY count DESC
                LIMIT 50
                """,
                since,
            )

            countries = [
                {
                    "country": r["country_name"],
                    "country_code": r["country_code"],
                    "count": r["count"],
                }
                for r in country_rows
            ]

            # Get city distribution (AVG coords to merge same city with different lat/lon)
            city_rows = await conn.fetch(
                """
                SELECT city, country_name,
                       AVG(latitude) as latitude,
                       AVG(longitude) as longitude,
                       COUNT(*) as count
                FROM ip_metadata
                WHERE created_at >= $1 AND city IS NOT NULL AND latitude IS NOT NULL
                GROUP BY city, country_name
                ORDER BY count DESC
                LIMIT 100
                """,
                since,
            )

            cities = []

            # Fetch all users grouped by city in a single query (avoids N+1)
            city_users_map: dict = {}
            try:
                # Join user_connections (INET) with ip_metadata (VARCHAR)
                # Use host() to strip CIDR mask from INET, with text fallback
                user_city_rows = await conn.fetch(
                    """
                    SELECT im.city, im.country_name,
                           u.username, u.uuid::text as uuid, u.status,
                           COUNT(uc.id) as connections,
                           array_agg(DISTINCT SPLIT_PART(uc.ip_address::text, '/', 1)) as ips
                    FROM user_connections uc
                    JOIN ip_metadata im
                        ON SPLIT_PART(uc.ip_address::text, '/', 1) = TRIM(im.ip_address)
                    JOIN users u ON uc.user_uuid = u.uuid
                    WHERE im.city IS NOT NULL AND im.country_name IS NOT NULL
                          AND im.created_at >= $1
                    GROUP BY im.city, im.country_name, u.uuid, u.username, u.status
                    ORDER BY im.city, connections DESC
                    """,
                    since,
                )
                for ur in user_city_rows:
                    key = (ur["city"], ur["country_name"])
                    if key not in city_users_map:
                        city_users_map[key] = []
                    city_users_map[key].append({
                        "username": ur["username"],
                        "uuid": ur["uuid"],
                        "status": ur["status"],
                        "connections": ur["connections"],
                        "ips": [str(ip) for ip in (ur["ips"] or [])],
                    })
                logger.info(
                    "Geo users: found %d user-city pairs across %d cities",
                    len(user_city_rows),
                    len(city_users_map),
                )
            except Exception as exc:
                logger.warning("Failed to fetch users by city: %s", exc)

            for r in city_rows:
                if r["latitude"] is None or r["longitude"] is None:
                    continue
                key = (r["city"], r["country_name"])
                users = city_users_map.get(key, [])
                cities.append({
                    "city": r["city"],
                    "country": r["country_name"],
                    "lat": float(r["latitude"]),
                    "lon": float(r["longitude"]),
                    "count": r["count"],
                    "unique_users": len(users),
                    "users": users,
                })

            return {"countries": countries, "cities": cities}

    except Exception as e:
        logger.error("get_geo_connections failed: %s", e)
        return {"countries": [], "cities": []}


@router.get("/top-users")
@limiter.limit(RATE_ANALYTICS)
async def get_top_users_by_traffic(
    request: Request,
    limit: int = Query(20, ge=5, le=100),
    admin: AdminUser = Depends(require_permission("analytics", "view")),
):
    """Get top users by traffic consumption."""
    return await _compute_top_users(limit=limit)


@cached("analytics:top-users", ttl=CACHE_TTL_LONG, key_args=("limit",))
async def _compute_top_users(limit: int = 20):
    """Compute top users by traffic (cacheable)."""
    try:
        from shared.database import db_service
        if not db_service.is_connected:
            return {"items": []}

        async with db_service.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT uuid, username, status,
                       used_traffic_bytes,
                       traffic_limit_bytes,
                       COALESCE(
                           raw_data->'userTraffic'->>'onlineAt',
                           raw_data->>'onlineAt'
                       ) as online_at
                FROM users
                WHERE used_traffic_bytes > 0
                ORDER BY used_traffic_bytes DESC
                LIMIT $1
                """,
                limit,
            )

            items = []
            for r in rows:
                used = r["used_traffic_bytes"] or 0
                limit_bytes = r["traffic_limit_bytes"]
                usage_pct = None
                if limit_bytes and limit_bytes > 0:
                    usage_pct = round((used / limit_bytes) * 100, 1)

                items.append({
                    "uuid": str(r["uuid"]),
                    "username": r["username"],
                    "status": r["status"],
                    "used_traffic_bytes": used,
                    "traffic_limit_bytes": limit_bytes,
                    "usage_percent": usage_pct,
                    "online_at": r["online_at"],
                })

            return {"items": items}

    except Exception as e:
        logger.error("get_top_users_by_traffic failed: %s", e)
        return {"items": []}


@router.get("/trends")
@limiter.limit(RATE_ANALYTICS)
async def get_trends(
    request: Request,
    metric: str = Query("users", description="Metric: users, traffic, violations"),
    period: str = Query("30d", description="Period: 7d, 30d, 90d"),
    date_from: Optional[str] = Query(None, description="Custom start date (ISO 8601)"),
    date_to: Optional[str] = Query(None, description="Custom end date (ISO 8601)"),
    admin: AdminUser = Depends(require_permission("analytics", "view")),
):
    """Get trend data — growth of users, traffic, violations over time."""
    return await _compute_trends(metric=metric, period=period, date_from=date_from, date_to=date_to)


@cached("analytics:trends", ttl=CACHE_TTL_LONG, key_args=("metric", "period", "date_from", "date_to"))
async def _compute_trends(metric: str = "users", period: str = "30d", date_from: Optional[str] = None, date_to: Optional[str] = None):
    """Compute trends (cacheable)."""
    try:
        from shared.database import db_service
        if not db_service.is_connected:
            return {"series": [], "total_growth": 0}

        now = datetime.now(timezone.utc)
        if date_from:
            since = datetime.fromisoformat(date_from.replace("Z", "+00:00"))
        else:
            delta_map = {"7d": 7, "30d": 30, "90d": 90}
            days = delta_map.get(period, 30)
            since = now - timedelta(days=days)

        async with db_service.acquire() as conn:
            if metric == "users":
                rows = await conn.fetch(
                    """
                    SELECT DATE(created_at) as day, COUNT(*) as count
                    FROM users
                    WHERE created_at >= $1
                    GROUP BY DATE(created_at)
                    ORDER BY day
                    """,
                    since,
                )
                series = [{"date": str(r["day"]), "value": r["count"]} for r in rows]

                # Total growth
                total_before = await conn.fetchval(
                    "SELECT COUNT(*) FROM users WHERE created_at < $1", since
                )
                total_now = await conn.fetchval("SELECT COUNT(*) FROM users")
                growth = total_now - (total_before or 0)

            elif metric == "violations":
                rows = await conn.fetch(
                    """
                    SELECT DATE(detected_at) as day, COUNT(*) as count
                    FROM violations
                    WHERE detected_at >= $1
                    GROUP BY DATE(detected_at)
                    ORDER BY day
                    """,
                    since,
                )
                series = [{"date": str(r["day"]), "value": r["count"]} for r in rows]
                growth = sum(s["value"] for s in series)

            elif metric == "traffic":
                # Approximate: sum of used_traffic_bytes from users created in each day
                rows = await conn.fetch(
                    """
                    SELECT DATE(created_at) as day,
                           SUM(used_traffic_bytes) as total_bytes
                    FROM users
                    WHERE created_at >= $1
                    GROUP BY DATE(created_at)
                    ORDER BY day
                    """,
                    since,
                )
                series = [
                    {"date": str(r["day"]), "value": int(r["total_bytes"] or 0)}
                    for r in rows
                ]
                growth = sum(s["value"] for s in series)

            else:
                series = []
                growth = 0

            return {
                "series": series,
                "metric": metric,
                "period": period,
                "total_growth": growth,
            }

    except Exception as e:
        logger.error("get_trends failed: %s", e)
        return {"series": [], "total_growth": 0}


@router.get("/shared-hwids")
@limiter.limit(RATE_ANALYTICS)
async def get_shared_hwids(
    request: Request,
    min_users: int = Query(2, ge=2, le=10),
    limit: int = Query(50, ge=5, le=200),
    admin: AdminUser = Depends(require_permission("analytics", "view")),
):
    """Get HWIDs shared across multiple user accounts."""
    return await _compute_shared_hwids(min_users=min_users, limit=limit)


@cached("analytics:shared-hwids", ttl=CACHE_TTL_LONG, key_args=("min_users", "limit"))
async def _compute_shared_hwids(min_users: int = 2, limit: int = 50):
    """Compute shared HWIDs (cacheable)."""
    try:
        from shared.database import db_service
        if not db_service.is_connected:
            return {"items": [], "total_shared_hwids": 0}

        items = await db_service.get_shared_hwids(min_users=min_users, limit=limit)
        return {"items": items, "total_shared_hwids": len(items)}

    except Exception as e:
        logger.error("get_shared_hwids failed: %s", e)
        return {"items": [], "total_shared_hwids": 0}


@router.get("/providers")
@limiter.limit(RATE_ANALYTICS)
async def get_providers(
    request: Request,
    period: str = Query("7d", description="Period: 24h, 7d, 30d"),
    admin: AdminUser = Depends(require_permission("analytics", "view")),
):
    """Get provider/ASN analytics from ip_metadata."""
    return await _compute_providers(period=period)


@cached("analytics:providers", ttl=CACHE_TTL_LONG, key_args=("period",))
async def _compute_providers(period: str = "7d"):
    """Compute provider analytics (cacheable)."""
    try:
        from shared.database import db_service
        if not db_service.is_connected:
            return {"connection_types": [], "top_asn": [], "flags": {}}

        now = datetime.now(timezone.utc)
        delta_map = {"24h": 1, "7d": 7, "30d": 30}
        days = delta_map.get(period, 7)
        since = now - timedelta(days=days)

        async with db_service.acquire() as conn:
            total = await conn.fetchval(
                "SELECT COUNT(*) FROM ip_metadata WHERE created_at >= $1",
                since,
            ) or 1

            # Connection types distribution
            type_rows = await conn.fetch(
                """
                SELECT COALESCE(connection_type, 'unknown') as type,
                       COUNT(*) as count
                FROM ip_metadata
                WHERE created_at >= $1
                GROUP BY connection_type
                ORDER BY count DESC
                """,
                since,
            )
            connection_types = [
                {"type": r["type"], "count": r["count"],
                 "percent": round(r["count"] / total * 100, 1)}
                for r in type_rows
            ]

            # Top ASN organizations
            asn_rows = await conn.fetch(
                """
                SELECT asn, asn_org, COUNT(*) as count
                FROM ip_metadata
                WHERE created_at >= $1 AND asn IS NOT NULL
                GROUP BY asn, asn_org
                ORDER BY count DESC
                LIMIT 10
                """,
                since,
            )
            top_asn = [
                {"asn": r["asn"], "org": r["asn_org"] or f"AS{r['asn']}",
                 "count": r["count"],
                 "percent": round(r["count"] / total * 100, 1)}
                for r in asn_rows
            ]

            # Flags: VPN/Proxy/Tor/Hosting percentages
            flag_row = await conn.fetchrow(
                """
                SELECT
                    COUNT(*) FILTER (WHERE is_vpn = true) as vpn,
                    COUNT(*) FILTER (WHERE is_proxy = true) as proxy,
                    COUNT(*) FILTER (WHERE is_tor = true) as tor,
                    COUNT(*) FILTER (WHERE is_hosting = true) as hosting
                FROM ip_metadata
                WHERE created_at >= $1
                """,
                since,
            )
            flags = {
                "vpn": {"count": flag_row["vpn"], "percent": round(flag_row["vpn"] / total * 100, 1)},
                "proxy": {"count": flag_row["proxy"], "percent": round(flag_row["proxy"] / total * 100, 1)},
                "tor": {"count": flag_row["tor"], "percent": round(flag_row["tor"] / total * 100, 1)},
                "hosting": {"count": flag_row["hosting"], "percent": round(flag_row["hosting"] / total * 100, 1)},
            }

            return {"connection_types": connection_types, "top_asn": top_asn, "flags": flags, "total": total}

    except Exception as e:
        logger.error("get_providers failed: %s", e)
        return {"connection_types": [], "top_asn": [], "flags": {}}


@router.get("/retention")
@limiter.limit(RATE_ANALYTICS)
async def get_retention(
    request: Request,
    weeks: int = Query(12, ge=4, le=52, description="Number of weeks to analyze"),
    admin: AdminUser = Depends(require_permission("analytics", "view")),
):
    """Get cohort retention analysis."""
    return await _compute_retention(weeks=weeks)


@cached("analytics:retention", ttl=CACHE_TTL_LONG, key_args=("weeks",))
async def _compute_retention(weeks: int = 12):
    """Compute retention cohorts (cacheable)."""
    try:
        from shared.database import db_service
        if not db_service.is_connected:
            return {"cohorts": [], "overall_retention": 0}

        now = datetime.now(timezone.utc)
        since = now - timedelta(weeks=weeks)

        async with db_service.acquire() as conn:
            # Get cohorts by registration week
            rows = await conn.fetch(
                """
                WITH cohorts AS (
                    SELECT
                        DATE_TRUNC('week', created_at)::date as cohort_week,
                        uuid,
                        status,
                        used_traffic_bytes,
                        expire_at
                    FROM users
                    WHERE created_at >= $1
                )
                SELECT
                    cohort_week,
                    COUNT(*) as total_users,
                    COUNT(*) FILTER (WHERE status = 'ACTIVE') as active_users,
                    COUNT(*) FILTER (WHERE used_traffic_bytes > 0) as with_traffic,
                    COUNT(*) FILTER (WHERE expire_at IS NOT NULL AND expire_at > NOW()) as with_active_sub
                FROM cohorts
                GROUP BY cohort_week
                ORDER BY cohort_week
                """,
                since,
            )

            cohorts = []
            total_registered = 0
            total_retained = 0

            for r in rows:
                total = r["total_users"]
                active = r["active_users"]
                retention_pct = round(active / total * 100, 1) if total > 0 else 0
                traffic_pct = round(r["with_traffic"] / total * 100, 1) if total > 0 else 0
                sub_pct = round(r["with_active_sub"] / total * 100, 1) if total > 0 else 0

                total_registered += total
                total_retained += active

                cohorts.append({
                    "week": str(r["cohort_week"]),
                    "total_users": total,
                    "active_users": active,
                    "retention_percent": retention_pct,
                    "with_traffic_percent": traffic_pct,
                    "with_active_sub_percent": sub_pct,
                })

            overall = round(total_retained / total_registered * 100, 1) if total_registered > 0 else 0

            return {
                "cohorts": cohorts,
                "overall_retention": overall,
                "total_registered": total_registered,
                "total_retained": total_retained,
            }

    except Exception as e:
        logger.error("get_retention failed: %s", e)
        return {"cohorts": [], "overall_retention": 0}


# ── Node Metrics History ────────────────────────────────────────

@router.get("/node-metrics-history")
@limiter.limit(RATE_ANALYTICS)
async def get_node_metrics_history(
    request: Request,
    period: str = Query("24h", description="Period: 24h, 7d, 30d"),
    node_uuid: Optional[str] = Query(None, description="Filter by node UUID"),
    admin: AdminUser = Depends(require_permission("fleet", "view")),
):
    """Get historical node metrics averages for the given period."""
    return await _compute_node_metrics_history(period=period, node_uuid=node_uuid)


@cached("analytics:node-metrics-history", ttl=300, key_args=("period", "node_uuid"))
async def _compute_node_metrics_history(period: str = "24h", node_uuid: Optional[str] = None):
    from shared.database import db_service

    try:
        if not db_service.is_connected:
            return {"nodes": [], "timeseries": []}

        nodes = await db_service.get_node_metrics_history(period=period, node_uuid=node_uuid)
        timeseries = await db_service.get_node_metrics_timeseries(period=period, node_uuid=node_uuid)

        # Group timeseries by bucket
        buckets: dict = defaultdict(dict)
        node_names: dict = {}
        for row in timeseries:
            b = row.get("bucket")
            bucket_str = b.isoformat() if hasattr(b, "isoformat") else str(b)
            uid = str(row["node_uuid"])
            node_names[uid] = row.get("node_name", uid[:8])
            buckets[bucket_str][uid] = {
                "cpu": float(row["avg_cpu"]) if row.get("avg_cpu") is not None else None,
                "memory": float(row["avg_memory"]) if row.get("avg_memory") is not None else None,
                "disk": float(row["avg_disk"]) if row.get("avg_disk") is not None else None,
            }

        ts_data = [{"timestamp": k, "nodes": v} for k, v in sorted(buckets.items())]

        return {
            "nodes": [
                {
                    "node_uuid": str(n["node_uuid"]),
                    "node_name": n.get("node_name", ""),
                    "avg_cpu": float(n["avg_cpu"]) if n.get("avg_cpu") is not None else None,
                    "avg_memory": float(n["avg_memory"]) if n.get("avg_memory") is not None else None,
                    "avg_disk": float(n["avg_disk"]) if n.get("avg_disk") is not None else None,
                    "max_cpu": float(n["max_cpu"]) if n.get("max_cpu") is not None else None,
                    "max_memory": float(n["max_memory"]) if n.get("max_memory") is not None else None,
                    "max_disk": float(n["max_disk"]) if n.get("max_disk") is not None else None,
                    "samples_count": n.get("samples_count", 0),
                }
                for n in nodes
            ],
            "timeseries": ts_data,
            "node_names": node_names,
        }
    except Exception as e:
        logger.error("get_node_metrics_history failed: %s", e)
        return {"nodes": [], "timeseries": []}
