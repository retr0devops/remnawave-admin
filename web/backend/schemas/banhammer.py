"""Schemas for Banhammer settings and tracking endpoints."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator


class BanhammerSettingsUpdateRequest(BaseModel):
    """Settings payload for Banhammer runtime."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    banhammer_enabled: bool = Field(
        default=False,
        validation_alias=AliasChoices("banhammer_enabled", "enabled", "is_enabled"),
    )
    banhammer_warning_limit: int = Field(
        default=3,
        ge=1,
        le=10,
        validation_alias=AliasChoices("banhammer_warning_limit", "warning_limit"),
    )
    banhammer_warning_cooldown_seconds: int = Field(
        default=60,
        ge=1,
        le=3600,
        validation_alias=AliasChoices(
            "banhammer_warning_cooldown_seconds",
            "warning_cooldown_seconds",
            "warning_cooldown_sec",
        ),
    )
    banhammer_block_stages_minutes: List[int] = Field(
        default_factory=lambda: [15, 60, 360, 720, 1440],
        validation_alias=AliasChoices("banhammer_block_stages_minutes", "block_stages_minutes"),
    )
    banhammer_warning_template: str = Field(
        default=(
            "Banhammer warning: node network policy mismatch detected. "
            "Reconnect using an allowed network type."
        ),
        max_length=4000,
        validation_alias=AliasChoices("banhammer_warning_template", "warning_template"),
    )

    @field_validator("banhammer_block_stages_minutes")
    @classmethod
    def _validate_block_stages(cls, values: List[int]) -> List[int]:
        cleaned: List[int] = []
        for raw in values:
            try:
                minutes = int(raw)
            except (TypeError, ValueError):
                raise ValueError("banhammer_block_stages_minutes must contain only integers")
            if minutes <= 0:
                raise ValueError("banhammer_block_stages_minutes values must be greater than zero")
            cleaned.append(minutes)
        if not cleaned:
            raise ValueError("banhammer_block_stages_minutes must contain at least one value")
        if len(cleaned) > 10:
            raise ValueError("banhammer_block_stages_minutes supports at most 10 stages")
        return cleaned


class BanhammerSettingsResponse(BaseModel):
    banhammer_enabled: bool
    banhammer_warning_limit: int
    banhammer_warning_cooldown_seconds: int
    banhammer_block_stages_minutes: List[int]
    banhammer_warning_template: str


class BanhammerEventItem(BaseModel):
    id: int
    user_uuid: str
    username: Optional[str] = None
    email: Optional[str] = None
    event_type: str
    warning_number: Optional[int] = None
    block_stage: Optional[int] = None
    block_minutes: Optional[int] = None
    blocked_until: Optional[datetime] = None
    message: Optional[str] = None
    details: Dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class BanhammerEventsResponse(BaseModel):
    items: List[BanhammerEventItem]
    total: int
    page: int
    per_page: int
    pages: int


class BanhammerStateItem(BaseModel):
    user_uuid: str
    username: Optional[str] = None
    email: Optional[str] = None
    user_status: Optional[str] = None
    warnings_count: int
    block_stage: int
    blocked_until: Optional[datetime] = None
    pre_block_status: Optional[str] = None
    last_warning_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime
    is_blocked: bool


class BanhammerStatesResponse(BaseModel):
    items: List[BanhammerStateItem]
    total: int
    page: int
    per_page: int
    pages: int
