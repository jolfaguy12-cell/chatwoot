"""Queue handler: turns merged webhook payloads into a graph run and delivers
the outcome to Chatwoot (reply / clarify / refuse / handoff)."""

import logging
import time
from datetime import datetime

from app import crud
from app.agent import prompt_store
from app.agent.graph import get_graph
from app.db import db_session
from app.services import (basalam_client, chatwoot_client, hub_client, iran_calendar,
                          runlog, stt)

log = logging.getLogger(__name__)

HISTORY_ROLE = {0: "user", 1: "assistant", 3: "assistant"}  # incoming/outgoing/template


async def handle_messages(conversation_id: int, payloads: list[dict]) -> None:
    """Entry point wired into the queue worker. Raises on transient failures
    (queue retries); terminal failures inside the graph become handoffs."""
    with db_session() as session:
        conv = crud.get_conversation(session, conversation_id)
        if conv.mode != "ai" or not crud.get_setting(session, "ai_enabled"):
            log.info("skipping run for conversation %s (mode=%s)", conversation_id, conv.mode)
            return
        contact = {"id": conv.contact_id, "name": conv.contact_name,
                   "phone_norm": conv.contact_phone_norm, "email": conv.contact_email}
        page_url, product_slug = conv.page_url, conv.product_slug
        cart = conv.cart or {}
        shown_products = list(conv.shown_products or [])
        history_limit = int(crud.get_setting(session, "history_limit") or 12)
        inbox_id = conv.inbox_id
    # Everything this run reads from the store comes from the hub behind that inbox.
    hub_client.use_inbox(inbox_id)

    texts: list[str] = []
    message_ids: list[int] = []
    for payload in payloads:
        if payload.get("message_id"):
            message_ids.append(payload["message_id"])
        if payload.get("content"):
            texts.append(payload["content"])
        for att in payload.get("attachments") or []:
            if att.get("file_type") == "audio" and att.get("data_url"):
                transcript = await _transcribe(conversation_id, att)
                if transcript:
                    texts.append(transcript)
                else:
                    await _send(conversation_id, prompt_store.get("msg_voice_unclear")[0])
    text = "\n".join(t for t in texts if t).strip()
    if not text:
        return

    messages = await _fetch_messages(conversation_id)
    if _operator_active(messages):
        log.info("operator is answering conversation %s from the Basalam panel — staying quiet",
                 conversation_id)
        return

    # one request split across several bubbles: coach the customer, and after
    # FRAGMENT_LIMIT turns of it stop answering and let a human decide
    fragmented = sum(1 for p in payloads if (p.get("content") or "").strip()) > 1
    if fragmented and _count_fragmented_turn(conversation_id) >= FRAGMENT_LIMIT:
        await _escalate_fragmented(conversation_id, text)
        return

    history = _build_history(messages, exclude_ids=set(message_ids), limit=history_limit)
    reply_context = _reply_context(
        messages, [p.get("in_reply_to") for p in payloads if p.get("in_reply_to")])

    await chatwoot_client.toggle_typing(conversation_id, "on")
    try:
        state = await get_graph().ainvoke({
            "conversation_id": conversation_id,
            "text": text,
            "contact": contact,
            "page_url": page_url,
            "product_slug": product_slug,
            "cart": cart,
            "shown_products": shown_products,
            "history": history,
            "reply_context": reply_context,
            "fragmented": fragmented,
            "greeting_allowed": not _greeted_today(messages, set(message_ids)),
        })
    finally:
        await chatwoot_client.toggle_typing(conversation_id, "off")

    outcome = state.get("outcome") or "reply"
    final_text = state.get("final_text") or ""
    response_id = runlog.write_run(conversation_id,
                                   message_ids[-1] if message_ids else None, dict(state))
    log.info("conversation %s outcome=%s response_id=%s", conversation_id, outcome, response_id)

    if outcome == "handoff":
        from app.services import handoff as handoff_service

        if final_text:
            await _send(conversation_id, final_text)
        await handoff_service.initiate(
            conversation_id,
            reason=state.get("handoff_reason") or "agent could not answer safely",
            reason_kind=state.get("handoff_kind") or "other",
            context={"last_message": text[:500],
                     "intent": state.get("intent", ""),
                     "product": state.get("resolved_product", "")},
        )
        return
    # cards first: the customer sees the products, then the text that wraps them up
    await _send_cards(conversation_id, state.get("cards") or [])
    if final_text:
        await _send(conversation_id, final_text)
    await _send_alerts(conversation_id, state.get("alerts") or [])


async def _send_alerts(conversation_id: int, alerts: list[dict]) -> None:
    """Admin notices raised by tools — they inform, they do not hand over."""
    if not alerts:
        return
    from app.services import telegram_bot

    for alert in alerts:
        try:
            await telegram_bot.alert_admins(conversation_id, alert)
        except Exception:  # noqa: BLE001 — an unsent alert must not break the reply
            log.exception("could not send admin alert for conversation %s", conversation_id)


async def _send_cards(conversation_id: int, cards: list[dict]) -> None:
    """کارت‌های صف‌شدهٔ پاسخِ **تأییدشده** را از پل باسلام می‌فرستد.

    ارسال عمداً اینجاست و نه داخل ابزار: پاسخی که در validation رد می‌شود کارتی
    پیش مشتری جا نمی‌گذارد، و بازنویسی از صفر کارت می‌سازد.
    """
    basalam_ids = [c["basalam_id"] for c in cards if isinstance(c.get("basalam_id"), int)]
    if not basalam_ids:
        return
    try:
        await basalam_client.send_product_cards(conversation_id, basalam_ids)
    except Exception:  # noqa: BLE001 — the text answer still goes out after this
        log.exception("could not send product cards to conversation %s", conversation_id)
        return
    # remember what was actually delivered, so the next answer never repeats it —
    # in both id spaces, because the model may name either one next time
    sent = [i for c in cards for i in (c.get("_ids") or []) if isinstance(i, int)]
    if sent:
        with db_session() as session:
            conv = crud.get_conversation(session, conversation_id)
            conv.shown_products = list(dict.fromkeys((conv.shown_products or []) + sent))


async def _send(conversation_id: int, content: str) -> None:
    await chatwoot_client.send_message(conversation_id, content)


async def _transcribe(conversation_id: int, attachment: dict) -> str | None:
    text, telemetry = await stt.transcribe_url(attachment["data_url"])
    runlog.write_single(
        kind="stt", role="stt", conversation_id=conversation_id,
        model_used=telemetry.get("model_used", ""),
        input_tokens=telemetry.get("input_tokens", 0),
        output_tokens=telemetry.get("output_tokens", 0),
        cost_usd=telemetry.get("cost_usd", 0.0),
        latency_ms=telemetry.get("latency_ms", 0),
        status="ok" if text else "error", error=telemetry.get("error", ""),
        output_text=text or "",
    )
    if text:
        # Operators see the transcript alongside the original voice note.
        try:
            await chatwoot_client.send_message(
                conversation_id, f"🎙️ **Voice transcription**\n{text}", private=True)
        except Exception:  # noqa: BLE001 — transcript note is best-effort
            log.exception("failed to post transcript note")
    return text


# how many fragmented turns are tolerated before a human takes over
FRAGMENT_LIMIT = 3


def _count_fragmented_turn(conversation_id: int) -> int:
    with db_session() as session:
        conv = crud.get_conversation(session, conversation_id)
        conv.fragmented_turns += 1
        return conv.fragmented_turns


async def _escalate_fragmented(conversation_id: int, text: str) -> None:
    """Label the conversation as spam and hand it to a human over Telegram."""
    from app.services import handoff as handoff_service

    try:
        await chatwoot_client.add_labels(conversation_id, ["spam"])
    except Exception:  # noqa: BLE001 — the handoff matters more than the label
        log.exception("could not label conversation %s as spam", conversation_id)
    await _send(conversation_id, prompt_store.get("msg_handoff")[0])
    await handoff_service.initiate(
        conversation_id,
        reason=f"customer sent fragmented messages {FRAGMENT_LIMIT} times (possible spam)",
        reason_kind="spam",
        context={"last_message": text[:500]},
    )


async def _fetch_messages(conversation_id: int) -> list[dict]:
    try:
        return await chatwoot_client.get_messages(conversation_id)
    except Exception:  # noqa: BLE001 — history is helpful, not critical
        log.warning("could not load history for conversation %s", conversation_id)
        return []


# An operator replying from the Basalam panel never touches Chatwoot, so the
# conversation stays 'pending' and the bot would talk over them. Those replies
# reach Chatwoot mirrored by the bridge, which is how we recognise them — the
# bridge tags the agent's own product cards `bslm:bot-…` so they don't count.
OPERATOR_SOURCE_PREFIX = "bslm:"
AGENT_SOURCE_PREFIX = "bslm:bot-"
OPERATOR_ACTIVE_SECONDS = 30 * 60


def _operator_active(messages: list[dict]) -> bool:
    now = time.time()
    for msg in messages:
        source_id = str(msg.get("source_id") or "")
        if (msg.get("message_type") == 1 and not msg.get("private")
                and source_id.startswith(OPERATOR_SOURCE_PREFIX)
                and not source_id.startswith(AGENT_SOURCE_PREFIX)
                and now - (msg.get("created_at") or 0) < OPERATOR_ACTIVE_SECONDS):
            return True
    return False


def _reply_context(messages: list[dict], reply_ids: list) -> str:
    """What the customer replied to. Without it a reply on a product card starts
    blind — «تاریخ انقضاش کیه؟» carries no clue about which product."""
    by_id = {m.get("id"): m for m in messages}
    parts = []
    for reply_id in reply_ids:
        msg = by_id.get(reply_id)
        if not msg:
            continue
        cards = (msg.get("content_attributes") or {}).get("items") or []
        titles = [(c.get("title") or "").strip() for c in cards]
        titles = [t for t in titles if t]
        if titles:
            parts.append("کارت محصول: " + "، ".join(titles))
        elif (msg.get("content") or "").strip():
            parts.append("پیام: " + msg["content"].strip()[:HISTORY_MESSAGE_CHARS])
    return " | ".join(parts)


# Only *stale* turns are labelled. Tagging every message got the model to copy
# the tag into its own reply («[همین الان] خیلی خوشحالم که…»), and the label
# only ever mattered for threads old enough to be closed.
STALE_AFTER_SECONDS = 30 * 60


def _age_label(created_at: float, now: float) -> str:
    """How long ago a history message was written, for old messages only.
    Without it a five-day-old delivery thread reads as live and a bare «ممنون»
    re-opens it."""
    seconds = max(0, int(now - (created_at or now)))
    if seconds < STALE_AFTER_SECONDS:
        return ""
    if seconds < 86400:
        return f"{seconds // 3600} ساعت پیش"
    return f"{seconds // 86400} روز پیش"


def _greeted_today(messages: list[dict], exclude_ids: set[int]) -> bool:
    """Has anything already gone out to this customer today (Tehran)?

    The greeting is a once-a-day thing, and «سلام… وقت بخیر» was landing in the
    middle of live threads. This is decided here rather than left to the model:
    the history it sees carries no clock, so it cannot tell today's turns from
    last week's. The inbox's automatic welcome bubble counts — the customer was
    greeted by it just the same.
    """
    today = iran_calendar.now().date()
    for msg in messages:
        if msg.get("id") in exclude_ids or msg.get("private"):
            continue
        if msg.get("message_type") not in (1, 3):  # outgoing / template
            continue
        created = msg.get("created_at") or 0
        if datetime.fromtimestamp(created, tz=iran_calendar.TEHRAN).date() == today:
            return True
    return False


def _build_history(messages: list[dict], *, exclude_ids: set[int],
                   limit: int) -> list[dict]:
    canned = _canned_openings()
    now = time.time()
    history = []
    for msg in messages:
        if msg.get("id") in exclude_ids or msg.get("private"):
            continue
        role = HISTORY_ROLE.get(msg.get("message_type"))
        content = msg.get("content")
        if not (role and content):
            continue
        # our own canned notices (handoff, apology, limit) must not become
        # examples the model imitates in its next answer
        if role == "assistant" and content[:40] in canned:
            continue
        label = _age_label(msg.get("created_at") or 0, now)
        body = content[:HISTORY_MESSAGE_CHARS]
        history.append({"role": role, "content": f"[{label}] {body}" if label else body})
    return _trim_history(history[-limit:])


CANNED_KEYS = ("msg_handoff", "msg_error_holding", "msg_limit_reached",
               "msg_refusal", "msg_voice_unclear", "msg_order_need_phone")


def _canned_openings() -> set[str]:
    return {prompt_store.get(key)[0][:40] for key in CANNED_KEYS}


# History is replayed into every LLM call, and our own product listings are long,
# so it is capped by size as well as by message count.
HISTORY_MESSAGE_CHARS = 400
HISTORY_TOTAL_CHARS = 3000


def _trim_history(history: list[dict]) -> list[dict]:
    kept: list[dict] = []
    size = 0
    for msg in reversed(history):
        size += len(msg["content"])
        if size > HISTORY_TOTAL_CHARS and kept:
            break
        kept.insert(0, msg)
    return kept


async def handle_failure(conversation_id: int, error: str) -> None:
    """Final-failure path from the queue: tell the customer, then hand off."""
    from app.agent.llm import is_quota_error
    from app.services import handoff as handoff_service

    hit_limit = is_quota_error(error)
    message_key = "msg_limit_reached" if hit_limit else "msg_error_holding"
    try:
        await _send(conversation_id, prompt_store.get(message_key)[0])
    except Exception:  # noqa: BLE001
        log.exception("could not send holding message to conversation %s", conversation_id)
    await handoff_service.initiate(
        conversation_id,
        reason=("AI provider usage limit reached: " if hit_limit
                else "AI processing failed repeatedly: ") + error[:300],
        reason_kind="provider_limit" if hit_limit else "provider_error", context={},
    )
