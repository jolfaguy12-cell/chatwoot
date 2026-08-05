"""Hub change webhooks — invalidate cached product/page data (≤60s freshness)."""

import logging

from fastapi import APIRouter, HTTPException, Request

from app.security import verify_hub_signature
from app.services import hub_client

log = logging.getLogger(__name__)
router = APIRouter()


@router.post("/webhooks/hub")
async def hub_webhook(request: Request):
    body = await request.body()
    if not verify_hub_signature(body, request.headers.get("X-BDSK-Signature")):
        raise HTTPException(status_code=401, detail="bad signature")
    payload = await request.json()
    event = payload.get("event", "")
    entity_id = payload.get("entity_id")
    if event.startswith("product."):
        hub_client.invalidate("product:")
        log.info("hub cache invalidated for products (event=%s id=%s)", event, entity_id)
    return {"ok": True}
