"""Bedolaga referrals — network graph, user/campaign details, search."""
from typing import Optional

from fastapi import APIRouter, Depends, Query, Path

from web.backend.api.deps import AdminUser, require_permission
from shared.bedolaga_client import bedolaga_client

from web.backend.api.v2.bedolaga import proxy_request

router = APIRouter()


@router.get("/network")
async def get_referral_network(
    admin: AdminUser = Depends(require_permission("bedolaga", "view")),
):
    """Глобальная реферальная сеть — все ноды и связи."""
    return await proxy_request(bedolaga_client.get_referral_network)


@router.get("/network/user/{user_id}")
async def get_referral_network_user(
    user_id: int = Path(...),
    admin: AdminUser = Depends(require_permission("bedolaga", "view")),
):
    """Детали пользователя в реферальной сети."""
    return await proxy_request(lambda: bedolaga_client.get_referral_network_user(user_id))


@router.get("/network/campaign/{campaign_id}")
async def get_referral_network_campaign(
    campaign_id: int = Path(...),
    admin: AdminUser = Depends(require_permission("bedolaga", "view")),
):
    """Детали кампании в реферальной сети."""
    return await proxy_request(lambda: bedolaga_client.get_referral_network_campaign(campaign_id))


@router.get("/network/search")
async def search_referral_network(
    q: str = Query(..., min_length=1),
    admin: AdminUser = Depends(require_permission("bedolaga", "view")),
):
    """Поиск в реферальной сети."""
    return await proxy_request(lambda: bedolaga_client.search_referral_network(q))
