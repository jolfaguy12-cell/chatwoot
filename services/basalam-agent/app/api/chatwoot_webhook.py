"""Chatwoot agent-bot webhook receiver.

Accepts signed events, filters echoes, syncs conversation control state and
enqueues incoming customer messages for the agent. Always acks fast — the
actual work happens in the queue worker.
"""

import hashlib
import logging

from fastapi import APIRouter, HTTPException, Request

from app import crud
from app.db import db_session
from app.models import ProcessedEvent
from app.security import verify_chatwoot_signature
from app.services import persian
from app.workers import queue

log = logging.getLogger(__name__)
router = APIRouter()

RELEVANT_EVENTS = {
    "message_created",
    "conversation_updated",
    "conversation_status_changed",
    "conversation_resolved",
    "conversation_opened",
}


@router.post("/webhooks/chatwoot")
async def chatwoot_webhook(request: Request):
    body = await request.body()
    if not verify_chatwoot_signature(
        body,
        request.headers.get("X-Chatwoot-Signature"),
        request.headers.get("X-Chatwoot-Timestamp"),
    ):
        raise HTTPException(status_code=401, detail="bad signature")

    payload = await request.json()
    event = payload.get("event", "")
    if event not in RELEVANT_EVENTS:
        return {"ok": True, "ignored": event}

    if not _dedup(event, payload):
        return {"ok": True, "duplicate": True}

    if event == "message_created":
        await _on_message_created(payload)
    else:
        _on_conversation_event(event, payload)
    return {"ok": True}


def _dedup(event: str, payload: dict) -> bool:
    """Insert-or-reject a dedup key; False means we've already seen this event."""
    if event == "message_created":
        raw = f"{event}:{payload.get('id')}"
    else:
        conv = payload.get("id") or (payload.get("conversation") or {}).get("id")
        status = payload.get("status", "")
        ts = payload.get("timestamp") or payload.get("updated_at") or ""
        raw = f"{event}:{conv}:{status}:{ts}"
    key = hashlib.sha256(raw.encode()).hexdigest()[:64]
    with db_session() as session:
        if session.get(ProcessedEvent, key):
            return False
        session.add(ProcessedEvent(event_key=key))
    return True


def _conversation_of(payload: dict) -> dict:
    return payload.get("conversation") or payload


def _sync_conversation(payload_conv: dict, sender: dict | None = None):
    """Mirror useful conversation fields into our control-state row."""
    display_id = payload_conv.get("id")
    if not display_id:
        return None, None
    custom = payload_conv.get("custom_attributes") or {}
    meta = payload_conv.get("meta") or {}
    assignee = meta.get("assignee") or {}
    with db_session() as session:
        conv = crud.get_conversation(session, int(display_id))
        if payload_conv.get("inbox_id"):
            conv.inbox_id = int(payload_conv["inbox_id"])
        contact = sender or (meta.get("sender") or {})
        if contact.get("id"):
            conv.contact_id = contact.get("id")
            conv.contact_name = contact.get("name") or conv.contact_name
            phone = persian.normalize_phone(contact.get("phone_number"))
            if phone:
                conv.contact_phone_norm = phone
            if contact.get("email"):
                conv.contact_email = contact["email"]
        if isinstance(custom.get("behdashtik_cart"), dict):
            conv.cart = custom["behdashtik_cart"]
        if custom.get("behdashtik_current_url"):
            conv.page_url = custom["behdashtik_current_url"]
            conv.product_slug = custom.get("behdashtik_product_slug") or (
                persian.slug_from_url(conv.page_url) or ""
            )
        mode = conv.mode
        status = payload_conv.get("status")
        assignee_id = assignee.get("id")
        return mode, {"display_id": int(display_id), "status": status,
                      "assignee_id": assignee_id}


# Typed by an operator in Chatwoot or in the Basalam panel, this hands the thread
# back to the agent. The bridge refuses to forward it to Basalam, and the bubble
# is deleted here, so it is a command to us and never customer-facing.
RESUME_KEYWORD = "ai"

# `bslm:bot-…` marks the cards the agent itself sent through the bridge; every
# other mirrored outgoing message was typed by a human in the Basalam panel.
AGENT_SOURCE_PREFIX = "bslm:bot-"


def _is_operator_message(payload: dict) -> bool:
    """Was this outgoing bubble written by a human, rather than by us?"""
    if str(payload.get("source_id") or "").startswith(AGENT_SOURCE_PREFIX):
        return False
    if (payload.get("sender") or {}).get("type") == "agent_bot":
        return False
    # replies relayed from the Telegram operator flow already move the mode there
    return not (payload.get("content_attributes") or {}).get("behdashtik_operator")


async def _on_operator_message(payload: dict) -> None:
    conv_payload = _conversation_of(payload)
    display_id = conv_payload.get("id")
    if not display_id or not _is_operator_message(payload):
        return
    from app.services import handoff as handoff_service

    display_id = int(display_id)
    if (payload.get("content") or "").strip().lower() == RESUME_KEYWORD:
        from app.services import chatwoot_client

        await handoff_service.return_to_ai(display_id, actor="operator", via="chat_keyword")
        await chatwoot_client.delete_message(display_id, payload.get("id"))
        log.info("conversation %s handed back to the agent by keyword", display_id)
        return
    if await handoff_service.taken_over_by_operator(display_id, via="chat"):
        log.info("operator took over conversation %s — agent stays quiet", display_id)


async def _on_message_created(payload: dict) -> None:
    if payload.get("private"):
        return
    if payload.get("message_type") == "outgoing":
        await _on_operator_message(payload)
        return
    if payload.get("message_type") != "incoming":
        return
    sender_type = (payload.get("sender") or {}).get("type", "")
    if sender_type == "agent_bot":
        return
    conv_payload = _conversation_of(payload)
    mode, info = _sync_conversation(conv_payload, payload.get("sender"))
    if info is None:
        return
    display_id = info["display_id"]

    with db_session() as session:
        ai_enabled = crud.get_setting(session, "ai_enabled")
    if not ai_enabled:
        log.info("ai disabled — ignoring message on conversation %s", display_id)
        return

    if mode == "human":
        from app.services import handoff as handoff_service

        await handoff_service.relay_customer_message(display_id, payload)
        return
    if mode == "disabled":
        return

    # AI mode. If a human took the conversation from the dashboard, respect it.
    if info["status"] == "open" and info["assignee_id"]:
        with db_session() as session:
            conv = crud.get_conversation(session, display_id)
            conv.mode = "human"
            crud.audit(session, "mode_changed", entity="conversation",
                       entity_id=display_id,
                       after={"mode": "human", "cause": "dashboard_assignment"})
        from app.services import handoff as handoff_service

        await handoff_service.relay_customer_message(display_id, payload)
        return

    queue.enqueue(display_id, payload.get("id"), _slim_message(payload))
    log.info("enqueued message %s for conversation %s", payload.get("id"), display_id)


def _slim_message(payload: dict) -> dict:
    """Keep only what the agent needs; webhook payloads are large."""
    conv = _conversation_of(payload)
    return {
        "message_id": payload.get("id"),
        "content": payload.get("content") or "",
        # which bubble the customer replied to — resolved to its content in the runner
        "in_reply_to": (payload.get("content_attributes") or {}).get("in_reply_to"),
        "attachments": [
            {"file_type": a.get("file_type"), "data_url": a.get("data_url"),
             "id": a.get("id"), "transcribed_text": a.get("transcribed_text")}
            for a in (payload.get("attachments") or [])
        ],
        "conversation_status": conv.get("status"),
        "created_at": payload.get("created_at"),
    }


HANDOFF_SETTLE_SECONDS = 15


def _handoff_is_settled(session, display_id: int) -> bool:
    """True when the newest active handoff is old enough that a pending-status
    event must be a deliberate human action, not our own handoff's event echo."""
    from datetime import datetime, timedelta, timezone

    from sqlalchemy import select

    from app.models import Handoff

    handoff = session.scalar(
        select(Handoff).where(Handoff.conversation_id == display_id,
                              Handoff.status.in_(["pending", "claimed"]))
        .order_by(Handoff.id.desc()))
    if handoff is None:
        return True
    created = handoff.created_at
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - created > timedelta(seconds=HANDOFF_SETTLE_SECONDS)


def _on_conversation_event(event: str, payload: dict) -> None:
    conv_payload = _conversation_of(payload)
    display_id = conv_payload.get("id")
    if not display_id:
        return
    _sync_conversation(conv_payload)
    status = conv_payload.get("status")

    with db_session() as session:
        conv = crud.get_conversation(session, int(display_id))
        if event == "conversation_resolved" or status == "resolved":
            from app.services.handoff import close_handoffs_sync

            close_handoffs_sync(session, int(display_id), "resolved")
            if conv.mode != "disabled":
                conv.mode = "ai"
                conv.assignee_operator_id = None
        elif (event == "conversation_status_changed" and status == "pending"
              and conv.mode == "human" and _handoff_is_settled(session, int(display_id))):
            # someone sent the conversation back to the bot from the dashboard.
            # Guarded against the race where our own pre-handoff reply emits a
            # conversation_updated that still carries the stale pending status.
            from app.services.handoff import close_handoffs_sync

            close_handoffs_sync(session, int(display_id), "returned")
            conv.mode = "ai"
            conv.assignee_operator_id = None
            crud.audit(session, "mode_changed", entity="conversation",
                       entity_id=display_id,
                       after={"mode": "ai", "cause": "status_pending"})
