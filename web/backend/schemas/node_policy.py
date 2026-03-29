"""Schemas for node-specific network policies."""
from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field


class NodeNetworkPolicyUpsertRequest(BaseModel):
    is_enabled: bool = True
    expected_connection_types: List[str] = Field(default_factory=list)
    strict_mode: bool = True
    violation_score: int = Field(default=70, ge=0, le=100)
    reason_template: Optional[str] = Field(default=None, max_length=1000)


class NodeNetworkPolicyItem(BaseModel):
    id: int
    node_uuid: str
    is_enabled: bool
    expected_connection_types: List[str]
    strict_mode: bool
    violation_score: int
    reason_template: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class NodeNetworkPolicyListResponse(BaseModel):
    items: List[NodeNetworkPolicyItem]
    total: int
