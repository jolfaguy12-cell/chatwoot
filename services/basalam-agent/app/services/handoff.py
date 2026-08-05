"""Human-handoff orchestration.

State model: conversations.mode is the single source of truth for who owns a
conversation ('ai' | 'human' | 'disabled'); Chatwoot mirrors it as status
pending (AI) / open (human queue or operator). Claims are atomic conditional
updates — exactly one operator wins.
"""

import asyncio
import logging
from datetime import datetime, timezone

from sqlalchemy import select, update

from app import crud
from app.db import db_session
from app.models import Conversation, Handoff, TelegramOperator
from app.services import chatwoot_client

log = logging.getLogger(__name__)


async def initiate(conversation_id: int, *, reason: str, reason_kind: str,
                   context: dict) -> int | None:
    """Move a conversation to human support and notify operators."""
    # these two are handoffs the customer is explicitly promised an answer to
    # («به انبار اطلاع دادم» / «نتیجه رو خدمتتون اعلام می‌کنن»), so they must be
    # recognisable at a glance in Telegram instead of a generic "AI decided"
    if reason in SELF_LABELLING_REASONS:
        reason_kind = reason
    with db_session() as session:
        conv = crud.get_conversation(session, conversation_id)
        existing = session.scalar(
            select(Handoff).where(Handoff.conversation_id == conversation_id,
                                  Handoff.status.in_(["pending", "claimed"]))
        )
        if existing:
            log.info("handoff already active for conversation %s", conversation_id)
            return existing.id
        conv.mode = "human"
        handoff = Handoff(conversation_id=conversation_id, reason=reason[:1000],
                          reason_kind=reason_kind, context=context or {})
        session.add(handoff)
        session.flush()
        handoff_id = handoff.id
        contact_name = conv.contact_name
        crud.audit(session, "handoff_initiated", entity="conversation",
                   entity_id=conversation_id,
                   after={"reason_kind": reason_kind, "reason": reason[:300]})

    try:
        await chatwoot_client.toggle_status(conversation_id, "open")
    except Exception:  # noqa: BLE001 — Chatwoot may already have opened it
        log.exception("could not open conversation %s in Chatwoot", conversation_id)

    try:
        from app.services import telegram_bot

        notified = await telegram_bot.notify_handoff(handoff_id, conversation_id,
                                                     contact_name=contact_name,
                                                     reason=reason, reason_kind=reason_kind,
                                                     context=context or {})
        if not notified:
            # the customer has already been told a human will get back to them;
            # a silent zero-operator fanout is the one failure nobody notices
            log.error("handoff %s (conversation %s, %s) reached NO telegram operator — "
                      "link one in Profile Settings → Telegram Connection",
                      handoff_id, conversation_id, reason_kind)
    except Exception:  # noqa: BLE001 — Telegram down must not break the handoff
        log.exception("telegram fanout failed for handoff %s", handoff_id)

    if reason == QOM_DISPATCH:
        asyncio.create_task(_reassure_if_unanswered(handoff_id, conversation_id))
    return handoff_id


# The customer was told we asked the warehouse; if nobody answered in time they
# deserve to hear that we are still on it rather than sit in silence.
QOM_DISPATCH = "qom_dispatch"
# «کی شارژ می‌شود؟» — only the warehouse knows, and the customer was told the
# answer will come back into this chat (app.agent.tools.ask_restock_date)
RESTOCK = "restock"
SELF_LABELLING_REASONS = (QOM_DISPATCH, RESTOCK)
REASSURE_AFTER_SECONDS = 600
REASSURE_TEXT = ("ببخشید که منتظر موندید 🙏 هنوز از انبار جواب نگرفتم؛ به محض اینکه"
                 " وضعیت ارسال امروز برای قم مشخص بشه، همین‌جا بهتون خبر می‌دم.")


async def _reassure_if_unanswered(handoff_id: int, conversation_id: int) -> None:
    await asyncio.sleep(REASSURE_AFTER_SECONDS)
    with db_session() as session:
        handoff = session.get(Handoff, handoff_id)
        if handoff is None or handoff.status != "pending":
            return
        # stored naive-UTC; Chatwoot timestamps are unix epoch
        asked_at = handoff.created_at.replace(tzinfo=timezone.utc).timestamp()
    # an operator who answered straight from the Chatwoot inbox never claims the
    # handoff, so the status alone would have us talk over them
    try:
        messages = await chatwoot_client.get_messages(conversation_id)
    except Exception:  # noqa: BLE001 — losing this check is not worth the silence
        messages = []
    answered = any(m.get("message_type") == 1 and not m.get("private")
                   and m.get("created_at", 0) > asked_at
                   for m in messages)
    if answered:
        return
    try:
        await chatwoot_client.send_message(conversation_id, REASSURE_TEXT)
    except Exception:  # noqa: BLE001 — a missed reassurance must not raise
        log.exception("could not send qom reassurance for handoff %s", handoff_id)


def claim(handoff_id: int, operator_id: int, via: str = "telegram") -> dict | None:
    """Atomic claim. Returns claim info when this operator won, None otherwise."""
    now = datetime.now(timezone.utc)
    with db_session() as session:
        result = session.execute(
            update(Handoff)
            .where(Handoff.id == handoff_id, Handoff.status == "pending")
            .values(status="claimed", claimed_by=operator_id, claimed_via=via,
                    claimed_at=now)
        )
        if result.rowcount != 1:
            return None
        handoff = session.get(Handoff, handoff_id)
        operator = session.get(TelegramOperator, operator_id)
        conv = crud.get_conversation(session, handoff.conversation_id)
        conv.mode = "human"
        conv.assignee_operator_id = operator_id
        crud.audit(session, "handoff_claimed", actor=operator.chatwoot_user_name,
                   via=via, entity="handoff", entity_id=handoff_id,
                   after={"conversation_id": handoff.conversation_id})
        return {
            "handoff_id": handoff_id,
            "conversation_id": handoff.conversation_id,
            "chatwoot_user_id": operator.chatwoot_user_id,
            "operator_name": operator.chatwoot_user_name,
        }


async def claim_and_assign(handoff_id: int, operator_id: int,
                           via: str = "telegram") -> dict | None:
    info = claim(handoff_id, operator_id, via)
    if info is None:
        return None
    try:
        await chatwoot_client.assign_agent(info["conversation_id"], info["chatwoot_user_id"])
    except Exception:  # noqa: BLE001 — assignment mirror is best-effort; claim stands
        log.exception("could not mirror assignment to Chatwoot for handoff %s", handoff_id)
    return info


def close_handoffs_sync(session, conversation_id: int, status: str) -> list[int]:
    """Close all active handoffs for a conversation (resolved | returned).
    Caller owns the session/commit."""
    now = datetime.now(timezone.utc)
    rows = list(session.scalars(
        select(Handoff).where(Handoff.conversation_id == conversation_id,
                              Handoff.status.in_(["pending", "claimed"]))
    ))
    for row in rows:
        row.status = status
        row.closed_at = now
    return [r.id for r in rows]


async def taken_over_by_operator(conversation_id: int, *, via: str) -> bool:
    """An operator answered this conversation themselves — the agent steps aside
    and stays aside until someone types the resume keyword.

    This used to be a 30-minute activity window, and it expired mid-conversation:
    an operator handled a partnership request at 17:25 and 17:58, the customer
    wrote back at 19:14, and the agent cut in with its own out-of-scope notice
    (conversation 164, 2026-08-03). No Telegram alert here — the operator who
    triggered this is already in the thread.
    """
    with db_session() as session:
        conv = crud.get_conversation(session, conversation_id)
        if conv.mode != "ai":
            return False
        conv.mode = "human"
        crud.audit(session, "mode_changed", actor="operator", via=via,
                   entity="conversation", entity_id=conversation_id,
                   after={"mode": "human", "cause": "operator_reply"})
    try:
        await chatwoot_client.toggle_status(conversation_id, "open")
    except Exception:  # noqa: BLE001 — the mode flip is what actually silences us
        log.exception("could not open conversation %s in Chatwoot", conversation_id)
    return True


async def return_to_ai(conversation_id: int, *, actor: str, via: str) -> bool:
    with db_session() as session:
        conv = crud.get_conversation(session, conversation_id)
        close_handoffs_sync(session, conversation_id, "returned")
        conv.mode = "ai"
        conv.assignee_operator_id = None
        crud.audit(session, "returned_to_ai", actor=actor, via=via,
                   entity="conversation", entity_id=conversation_id)
    try:
        await chatwoot_client.toggle_status(conversation_id, "pending")
        await chatwoot_client.send_message(
            conversation_id, f"🤖 AI assistant resumed (by {actor})", private=True)
    except Exception:  # noqa: BLE001
        log.exception("could not restore pending status for conversation %s", conversation_id)
        return False
    return True


async def resolve(conversation_id: int, *, actor: str, via: str) -> bool:
    with db_session() as session:
        conv = crud.get_conversation(session, conversation_id)
        close_handoffs_sync(session, conversation_id, "resolved")
        conv.mode = "ai"
        conv.assignee_operator_id = None
        crud.audit(session, "conversation_resolved", actor=actor, via=via,
                   entity="conversation", entity_id=conversation_id)
    try:
        await chatwoot_client.toggle_status(conversation_id, "resolved")
    except Exception:  # noqa: BLE001
        log.exception("could not resolve conversation %s in Chatwoot", conversation_id)
        return False
    return True


async def relay_customer_message(conversation_id: int, payload: dict) -> None:
    """Forward a customer message in a human-mode conversation to the claiming
    operator's Telegram (per their notification preference)."""
    with db_session() as session:
        handoff = session.scalar(
            select(Handoff).where(Handoff.conversation_id == conversation_id,
                                  Handoff.status == "claimed")
            .order_by(Handoff.id.desc())
        )
        operator = session.get(TelegramOperator, handoff.claimed_by) if handoff else None
    if handoff is None:
        return  # unclaimed — visible in the Chatwoot open queue and /status
    text = payload.get("content") or ""
    attachments = payload.get("attachments") or []
    if attachments:
        text += "\n" + "\n".join(
            f"📎 {a.get('file_type')}: {a.get('data_url')}" for a in attachments)
    if not text.strip():
        return

    from app.services import telegram_bot

    if handoff and operator and operator.active and operator.pref in ("telegram", "both"):
        await telegram_bot.relay_to_operator(
            operator_chat_id=operator.telegram_chat_id,
            conversation_id=conversation_id, handoff_id=handoff.id, text=text)


def get_active_handoff(conversation_id: int) -> Handoff | None:
    with db_session() as session:
        return session.scalar(
            select(Handoff).where(Handoff.conversation_id == conversation_id,
                                  Handoff.status.in_(["pending", "claimed"]))
            .order_by(Handoff.id.desc())
        )
