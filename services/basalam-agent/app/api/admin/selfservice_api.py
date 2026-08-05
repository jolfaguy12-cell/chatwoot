"""Operator self-service (reachable by non-admin agents through the Chatwoot
proxy): Telegram linking and notification preferences. The acting operator is
identified by headers the Rails proxy injects — never by client-supplied JSON."""

import logging

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from app import crud
from app.config import get_settings
from app.db import db_session
from app.models import TelegramOperator
from app.services import telegram_bot

log = logging.getLogger(__name__)
router = APIRouter()


def _require_user(user_id: str) -> int:
    try:
        uid = int(user_id)
        if uid <= 0:
            raise ValueError
        return uid
    except (TypeError, ValueError):
        raise HTTPException(400, "missing X-Chatwoot-User-Id header") from None


def _self_out(op: TelegramOperator | None, bot_username: str) -> dict:
    return {
        "linked": op is not None,
        "telegram_username": op.telegram_username if op else None,
        "pref": op.pref if op else "both",
        "active": op.active if op else False,
        "linked_at": op.linked_at.isoformat() if op else None,
        "bot_username": bot_username,
    }


async def _bot_username() -> str:
    app = telegram_bot.get_application()
    if app is None:
        return ""
    me = getattr(app.bot, "_me_cached", None)
    if me is None:
        try:
            me = await app.bot.get_me()
            app.bot._me_cached = me
        except Exception:  # noqa: BLE001
            return ""
    return me.username or ""


@router.get("/telegram/self")
async def telegram_self(x_chatwoot_user_id: str = Header(default="")):
    uid = _require_user(x_chatwoot_user_id)
    with db_session() as session:
        op = session.scalar(select(TelegramOperator)
                            .where(TelegramOperator.chatwoot_user_id == uid))
    return _self_out(op, await _bot_username())


@router.post("/telegram/link_code")
async def telegram_link_code(x_chatwoot_user_id: str = Header(default=""),
                             x_chatwoot_user_name: str = Header(default="")):
    uid = _require_user(x_chatwoot_user_id)
    if not get_settings().telegram_bot_token:
        raise HTTPException(503, "telegram bot is not configured")
    code = telegram_bot.create_link_code(uid, x_chatwoot_user_name or f"operator {uid}")
    username = await _bot_username()
    return {"code": code,
            "deep_link": f"https://t.me/{username}?start={code}" if username else None,
            "expires_in_minutes": 10}


class PrefIn(BaseModel):
    pref: str


@router.patch("/telegram/self")
async def telegram_self_patch(body: PrefIn,
                              x_chatwoot_user_id: str = Header(default="")):
    uid = _require_user(x_chatwoot_user_id)
    if body.pref not in ("dashboard", "telegram", "both", "disabled"):
        raise HTTPException(400, "invalid pref")
    with db_session() as session:
        op = session.scalar(select(TelegramOperator)
                            .where(TelegramOperator.chatwoot_user_id == uid))
        if op is None:
            raise HTTPException(404, "telegram not linked")
        op.pref = body.pref
        crud.audit(session, "operator_pref_changed", actor=op.chatwoot_user_name,
                   via="dashboard", entity="operator", entity_id=op.id,
                   after={"pref": body.pref})
    return _self_out(op, await _bot_username())


@router.delete("/telegram/self")
async def telegram_self_delete(x_chatwoot_user_id: str = Header(default="")):
    uid = _require_user(x_chatwoot_user_id)
    with db_session() as session:
        op = session.scalar(select(TelegramOperator)
                            .where(TelegramOperator.chatwoot_user_id == uid))
        if op is None:
            return {"ok": True}
        session.delete(op)
        crud.audit(session, "telegram_unlinked", actor=op.chatwoot_user_name,
                   via="dashboard", entity="operator", entity_id=op.id)
    return {"ok": True}
