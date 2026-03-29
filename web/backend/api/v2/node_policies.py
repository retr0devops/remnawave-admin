"""Node-specific anti-abuse policies API."""
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, field_validator

from web.backend.api.deps import AdminUser, require_permission, get_db, get_client_ip
from web.backend.core.rbac import write_audit_log
from shared.database import DatabaseService

router = APIRouter()

ALLOWED_CONNECTION_TYPES = {
    "mobile",
    "mobile_isp",
    "fixed",
    "isp",
    "regional_isp",
    "residential",
    "hosting",
    "vpn",
    "business",
    "datacenter",
}


class NodePolicyUpsertRequest(BaseModel):
    is_enabled: bool = True
    expected_connection_types: list[str] = Field(default_factory=list)
    strict_mode: bool = True
    violation_score: int = Field(default=70, ge=0, le=100)
    reason_template: Optional[str] = Field(default=None, max_length=2000)

    @field_validator("expected_connection_types")
    @classmethod
    def normalize_connection_types(cls, v: list[str]) -> list[str]:
        return sorted(set(str(item).strip().lower() for item in (v or []) if str(item).strip()))


class NodePolicyResponse(BaseModel):
    id: int
    node_uuid: str
    is_enabled: bool
    expected_connection_types: list[str]
    strict_mode: bool
    violation_score: int
    reason_template: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


def _validated_connection_types(connection_types: list[str]) -> list[str]:
    invalid = sorted(set(connection_types or []) - ALLOWED_CONNECTION_TYPES)
    if invalid:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Unknown connection_type values: {', '.join(invalid)}. "
                f"Allowed: {', '.join(sorted(ALLOWED_CONNECTION_TYPES))}"
            ),
        )
    return connection_types


@router.get("", response_model=list[NodePolicyResponse])
async def list_node_policies(
    admin: AdminUser = Depends(require_permission("nodes", "view")),
    db: DatabaseService = Depends(get_db),
):
    if not db.is_connected:
        return []
    rows = await db.list_node_network_policies()
    return [NodePolicyResponse(**{**row, "node_uuid": str(row["node_uuid"])}) for row in rows]


@router.get("/{node_uuid}", response_model=NodePolicyResponse)
async def get_node_policy(
    node_uuid: str,
    admin: AdminUser = Depends(require_permission("nodes", "view")),
    db: DatabaseService = Depends(get_db),
):
    if not db.is_connected:
        raise HTTPException(status_code=503, detail="Database unavailable")
    row = await db.get_node_network_policy(node_uuid)
    if not row:
        raise HTTPException(status_code=404, detail="Node policy not found")
    return NodePolicyResponse(**{**row, "node_uuid": str(row["node_uuid"])})


@router.put("/{node_uuid}", response_model=NodePolicyResponse)
async def upsert_node_policy(
    node_uuid: str,
    payload: NodePolicyUpsertRequest,
    request: Request,
    admin: AdminUser = Depends(require_permission("nodes", "edit")),
    db: DatabaseService = Depends(get_db),
):
    if not db.is_connected:
        raise HTTPException(status_code=503, detail="Database unavailable")

    row = await db.upsert_node_network_policy(
        node_uuid=node_uuid,
        is_enabled=payload.is_enabled,
        expected_connection_types=_validated_connection_types(payload.expected_connection_types),
        strict_mode=payload.strict_mode,
        violation_score=payload.violation_score,
        reason_template=payload.reason_template,
    )
    if not row:
        raise HTTPException(status_code=500, detail="Failed to save node policy")

    await write_audit_log(
        admin_id=admin.account_id,
        admin_username=admin.username,
        action="node_policy.upsert",
        resource="nodes",
        resource_id=node_uuid,
        details=payload.model_dump_json(),
        ip_address=get_client_ip(request),
    )

    return NodePolicyResponse(**{**row, "node_uuid": str(row["node_uuid"])})


@router.delete("/{node_uuid}")
async def delete_node_policy(
    node_uuid: str,
    request: Request,
    admin: AdminUser = Depends(require_permission("nodes", "edit")),
    db: DatabaseService = Depends(get_db),
):
    if not db.is_connected:
        raise HTTPException(status_code=503, detail="Database unavailable")
    deleted = await db.delete_node_network_policy(node_uuid)
    if not deleted:
        raise HTTPException(status_code=404, detail="Node policy not found")

    await write_audit_log(
        admin_id=admin.account_id,
        admin_username=admin.username,
        action="node_policy.delete",
        resource="nodes",
        resource_id=node_uuid,
        details="{}",
        ip_address=get_client_ip(request),
    )
    return {"status": "ok"}
