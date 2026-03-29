"""Node-specific network policy CRUD API."""
from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from web.backend.api.deps import AdminUser, get_client_ip, get_db, require_permission
from web.backend.core.errors import E, api_error
from web.backend.core.rbac import write_audit_log
from web.backend.schemas.common import SuccessResponse
from web.backend.schemas.node_policy import (
    NodeNetworkPolicyItem,
    NodeNetworkPolicyListResponse,
    NodeNetworkPolicyUpsertRequest,
)
from shared.connection_types import VALID_CONNECTION_TYPES, normalize_connection_types

logger = logging.getLogger(__name__)

router = APIRouter()


def _row_to_item(row: dict[str, Any]) -> NodeNetworkPolicyItem:
    expected = row.get("expected_connection_types") or []
    if isinstance(expected, str):
        try:
            expected = json.loads(expected)
        except Exception:
            expected = []

    return NodeNetworkPolicyItem(
        id=int(row["id"]),
        node_uuid=str(row["node_uuid"]),
        is_enabled=bool(row.get("is_enabled", True)),
        expected_connection_types=[str(x) for x in expected if str(x).strip()],
        strict_mode=bool(row.get("strict_mode", True)),
        violation_score=int(row.get("violation_score", 70) or 70),
        reason_template=row.get("reason_template"),
        created_at=row.get("created_at"),
        updated_at=row.get("updated_at"),
    )


def _validate_expected_connection_types(raw_values: list[str], *, is_enabled: bool) -> list[str]:
    normalized, invalid = normalize_connection_types(raw_values)
    if invalid:
        allowed = ", ".join(sorted(VALID_CONNECTION_TYPES))
        raise HTTPException(
            status_code=400,
            detail=f"Unknown connection_type values: {', '.join(sorted(invalid))}. Allowed: {allowed}",
        )
    if is_enabled and not normalized:
        raise HTTPException(
            status_code=400,
            detail="expected_connection_types must contain at least one valid value when policy is enabled",
        )
    return normalized


@router.get("", response_model=NodeNetworkPolicyListResponse)
async def list_node_policies(
    admin: AdminUser = Depends(require_permission("nodes", "view")),
    db=Depends(get_db),
):
    rows = await db.list_node_network_policies()
    items = [_row_to_item(row) for row in rows]
    return NodeNetworkPolicyListResponse(items=items, total=len(items))


@router.get("/{node_uuid}", response_model=NodeNetworkPolicyItem)
async def get_node_policy(
    node_uuid: str,
    admin: AdminUser = Depends(require_permission("nodes", "view")),
    db=Depends(get_db),
):
    row = await db.get_node_network_policy(node_uuid)
    if not row:
        raise api_error(404, E.NODE_NOT_FOUND, "Node policy not found")
    return _row_to_item(row)


@router.put("/{node_uuid}", response_model=NodeNetworkPolicyItem)
async def upsert_node_policy(
    node_uuid: str,
    data: NodeNetworkPolicyUpsertRequest,
    request: Request,
    admin: AdminUser = Depends(require_permission("nodes", "edit")),
    db=Depends(get_db),
):
    node = await db.get_node_by_uuid(node_uuid)
    if not node:
        raise api_error(404, E.NODE_NOT_FOUND)

    expected_connection_types = _validate_expected_connection_types(
        data.expected_connection_types,
        is_enabled=data.is_enabled,
    )
    reason_template = data.reason_template.strip() if data.reason_template else None

    row = await db.upsert_node_network_policy(
        node_uuid=node_uuid,
        is_enabled=data.is_enabled,
        expected_connection_types=expected_connection_types,
        strict_mode=data.strict_mode,
        violation_score=data.violation_score,
        reason_template=reason_template,
    )
    if not row:
        raise api_error(500, E.INTERNAL_ERROR, "Failed to save node policy")

    await write_audit_log(
        admin_id=admin.account_id,
        admin_username=admin.username,
        action="node_policy.upsert",
        resource="nodes",
        resource_id=node_uuid,
        details=json.dumps(
            {
                "is_enabled": data.is_enabled,
                "expected_connection_types": expected_connection_types,
                "strict_mode": data.strict_mode,
                "violation_score": data.violation_score,
                "reason_template": reason_template,
            }
        ),
        ip_address=get_client_ip(request),
    )

    return _row_to_item(row)


@router.delete("/{node_uuid}", response_model=SuccessResponse)
async def delete_node_policy(
    node_uuid: str,
    request: Request,
    admin: AdminUser = Depends(require_permission("nodes", "edit")),
    db=Depends(get_db),
):
    deleted = await db.delete_node_network_policy(node_uuid)
    if not deleted:
        raise api_error(404, E.NODE_NOT_FOUND, "Node policy not found")

    await write_audit_log(
        admin_id=admin.account_id,
        admin_username=admin.username,
        action="node_policy.delete",
        resource="nodes",
        resource_id=node_uuid,
        details=json.dumps({"node_uuid": node_uuid}),
        ip_address=get_client_ip(request),
    )

    return SuccessResponse(message="Node policy deleted")
