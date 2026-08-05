"""Telegram webhook — verified by X-Telegram-Bot-Api-Secret-Token, fed to PTB."""

import logging

from fastapi import APIRouter, HTTPException, Request

from app.security import verify_telegram_secret
from app.services import telegram_bot

log = logging.getLogger(__name__)
router = APIRouter()


@router.post("/telegram/webhook")
async def telegram_webhook(request: Request):
    if not verify_telegram_secret(request.headers.get("X-Telegram-Bot-Api-Secret-Token")):
        raise HTTPException(status_code=401, detail="bad secret token")
    data = await request.json()
    try:
        await telegram_bot.feed_update(data)
    except Exception:  # noqa: BLE001 — never make Telegram retry-storm us
        log.exception("telegram update processing failed")
    return {"ok": True}
