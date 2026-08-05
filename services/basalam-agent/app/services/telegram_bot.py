"""Telegram operator bot (python-telegram-bot v21, webhook-fed).

Operators link accounts with a one-time deep-link code generated from their
Chatwoot profile. Handoffs fan out with an inline Claim button; the first
claim wins atomically, other notifications are edited to show the winner.
Operators reply to relayed messages to answer the right conversation.
"""

import logging
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from app import crud
from app.config import get_settings
from app.db import db_session
from app.models import Handoff, LinkCode, TelegramMsgMap, TelegramOperator
from app.services import chatwoot_client

log = logging.getLogger(__name__)

_application: Application | None = None

LINK_CODE_TTL = timedelta(minutes=10)


# ---------------------------------------------------------------------------
# lifecycle
# ---------------------------------------------------------------------------

def get_application() -> Application | None:
    return _application


async def start_bot() -> None:
    global _application
    settings = get_settings()
    if not settings.telegram_bot_token:
        log.warning("telegram bot token not configured — operator bot disabled")
        return
    app = Application.builder().token(settings.telegram_bot_token).updater(None).build()
    app.add_handler(CommandHandler("start", _cmd_start))
    app.add_handler(CommandHandler("status", _cmd_status))
    app.add_handler(CommandHandler("help", _cmd_help))
    app.add_handler(CallbackQueryHandler(_on_callback))
    app.add_handler(MessageHandler(
        (filters.TEXT | filters.PHOTO | filters.Document.ALL | filters.VOICE)
        & ~filters.COMMAND, _on_message))
    await app.initialize()
    await app.bot.set_webhook(
        url=f"{settings.public_base_url}/telegram/webhook",
        secret_token=settings.telegram_webhook_secret,
        allowed_updates=["message", "callback_query"],
    )
    await app.start()
    _application = app
    log.info("telegram bot started (webhook mode)")


async def stop_bot() -> None:
    global _application
    if _application is not None:
        await _application.stop()
        await _application.shutdown()
        _application = None


async def feed_update(data: dict) -> None:
    if _application is None:
        return
    update = Update.de_json(data, _application.bot)
    await _application.process_update(update)


# ---------------------------------------------------------------------------
# linking
# ---------------------------------------------------------------------------

def create_link_code(chatwoot_user_id: int, chatwoot_user_name: str) -> str:
    code = secrets.token_hex(16)
    with db_session() as session:
        session.add(LinkCode(
            code=code, chatwoot_user_id=chatwoot_user_id,
            chatwoot_user_name=chatwoot_user_name,
            expires_at=datetime.now(timezone.utc) + LINK_CODE_TTL,
        ))
    return code


def _operator_by_chat(session, chat_id: int) -> TelegramOperator | None:
    return session.scalar(
        select(TelegramOperator).where(TelegramOperator.telegram_chat_id == chat_id))


async def _cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    args = context.args or []
    if not args:
        with db_session() as session:
            op = _operator_by_chat(session, chat_id)
        if op:
            await update.message.reply_text(
                f"✅ Linked as {op.chatwoot_user_name}. Preference: {op.pref}.\n"
                "You'll receive handoff notifications here.")
        else:
            await update.message.reply_text(
                "This bot serves Behdashtik support operators.\n"
                "Link your account from Chatwoot: Profile Settings → Telegram Connection.")
        return
    code = args[0].strip()
    now = datetime.now(timezone.utc)
    with db_session() as session:
        link = session.get(LinkCode, code)
        if (link is None or link.used_at is not None
                or link.expires_at.replace(tzinfo=timezone.utc) < now):
            await update.message.reply_text("❌ This link code is invalid or expired. "
                                            "Generate a new one from your Chatwoot profile.")
            return
        link.used_at = now
        op = session.scalar(select(TelegramOperator).where(
            TelegramOperator.chatwoot_user_id == link.chatwoot_user_id))
        user = update.effective_user
        if op is None:
            op = TelegramOperator(
                chatwoot_user_id=link.chatwoot_user_id,
                chatwoot_user_name=link.chatwoot_user_name,
                telegram_chat_id=chat_id,
                telegram_username=user.username or "",
            )
            session.add(op)
        else:
            op.telegram_chat_id = chat_id
            op.telegram_username = user.username or ""
            op.active = True
        crud.audit(session, "telegram_linked", actor=link.chatwoot_user_name,
                   via="telegram", entity="operator", entity_id=link.chatwoot_user_id)
        name = link.chatwoot_user_name
    await update.message.reply_text(
        f"✅ Telegram linked to Chatwoot operator «{name}».\n"
        "You'll now receive human-handoff notifications here. /help for commands.")


async def _cmd_help(update: Update, _context) -> None:
    await update.message.reply_text(
        "🤖 Behdashtik operator bot\n\n"
        "• Handoffs appear here with a Claim button.\n"
        "• Reply to a customer message to answer it in Chatwoot.\n"
        "• Buttons on your claimed console: ✅ Resolve, 🤖 Return to AI.\n"
        "• /status — your open handoffs.\n"
        "• Manage the connection from Chatwoot → Profile Settings.")


async def _cmd_status(update: Update, _context) -> None:
    chat_id = update.effective_chat.id
    with db_session() as session:
        op = _operator_by_chat(session, chat_id)
        if op is None:
            await update.message.reply_text("Not linked. Use Chatwoot → Profile Settings.")
            return
        pending = list(session.scalars(select(Handoff).where(Handoff.status == "pending")))
        mine = list(session.scalars(select(Handoff).where(
            Handoff.status == "claimed", Handoff.claimed_by == op.id)))
    lines = [f"Unclaimed handoffs: {len(pending)}"]
    for h in pending[:5]:
        lines.append(f"  • #{h.id} conv {h.conversation_id}: {h.reason[:60]}")
    lines.append(f"Your claimed conversations: {len(mine)}")
    for h in mine[:5]:
        lines.append(f"  • conv {h.conversation_id}")
    await update.message.reply_text("\n".join(lines))


# ---------------------------------------------------------------------------
# handoff fanout
# ---------------------------------------------------------------------------

def _conversation_link(conversation_id: int) -> str:
    settings = get_settings()
    return (f"{settings.chatwoot_public_url}/app/accounts/"
            f"{settings.chatwoot_account_id}/conversations/{conversation_id}")

REASON_LABELS = {
    "user_request": "Customer asked for a human",
    "missing_info": "Information missing",
    "validation_fail": "Reply failed quality checks",
    "provider_error": "AI provider error",
    "provider_limit": "⚠️ AI usage limit reached — AI is out of quota",
    "hub_error": "Store data unavailable",
    "order_auth_fail": "Order verification failed",
    "sensitive": "Sensitive issue",
    "spam": "🚩 Possible spam — message sent in fragments repeatedly",
    "agent_decision": "AI decided a human is needed",
    # the customer was promised an answer on these two — see handoff.SELF_LABELLING_REASONS
    "qom_dispatch": "🏭 قم — از انبار بپرس امروز ارسال می‌شود یا نه (به مشتری قول جواب داده‌ایم)",
    "restock": "📦 کی شارژ می‌شود؟ — از انبار بپرس و نتیجه را در همین گفتگو به مشتری بگو",
    "other": "Needs human attention",
}


async def notify_handoff(handoff_id: int, conversation_id: int, *, contact_name: str,
                         reason: str, reason_kind: str, context: dict) -> int:
    app = _application
    if app is None:
        log.warning("telegram bot not running — handoff %s not fanned out", handoff_id)
        return 0
    with db_session() as session:
        operators = list(session.scalars(select(TelegramOperator).where(
            TelegramOperator.active.is_(True),
            TelegramOperator.pref.in_(["telegram", "both"]))))
    if not operators:
        log.info("no telegram operators to notify for handoff %s", handoff_id)
        return 0
    label = REASON_LABELS.get(reason_kind, reason_kind)
    lines = [
        f"🆘 Human support needed — conversation #{conversation_id}",
        f"Customer: {contact_name or 'unknown'}",
        f"Reason: {label}",
    ]
    if context.get("product"):
        lines.append(f"Product: {context['product']}")
    if context.get("last_message"):
        lines.append(f"Last message: «{context['last_message'][:200]}»")
    lines.append(f"Open in Chatwoot: {_conversation_link(conversation_id)}")
    text = "\n".join(lines)
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("✋ Claim", callback_data=f"claim:{handoff_id}")]])
    sent = 0
    for op in operators:
        try:
            msg = await app.bot.send_message(op.telegram_chat_id, text,
                                             reply_markup=keyboard,
                                             disable_web_page_preview=True)
            with db_session() as session:
                session.add(TelegramMsgMap(
                    telegram_chat_id=op.telegram_chat_id, telegram_message_id=msg.message_id,
                    conversation_id=conversation_id, handoff_id=handoff_id, kind="notify"))
            sent += 1
        except Exception:  # noqa: BLE001 — one operator's failure must not stop fanout
            log.exception("could not notify operator %s", op.chatwoot_user_id)
    return sent


ALERT_LABELS = {
    "delivery_problem": "📮 مرسوله در باسلام «تحویل شده» است ولی مشتری می‌گوید نرسیده",
}


async def alert_admins(conversation_id: int, alert: dict) -> int:
    """اعلان اطلاعی برای ادمین‌ها، بدون هندآف.

    گفتگو دست ایجنت می‌ماند (راهنمایی ۱۹۳ و دلداری را خودش می‌دهد)؛ این فقط
    خبر می‌دهد که موضوعی پیش آمده که آدم باید پیگیری کند.
    """
    app = _application
    if app is None:
        log.warning("telegram bot not running — alert for conversation %s dropped", conversation_id)
        return 0
    with db_session() as session:
        operators = list(session.scalars(select(TelegramOperator).where(
            TelegramOperator.active.is_(True),
            TelegramOperator.pref.in_(["telegram", "both"]))))
    if not operators:
        log.error("alert %s on conversation %s reached NO telegram operator",
                  alert.get("kind"), conversation_id)
        return 0
    lines = [ALERT_LABELS.get(alert.get("kind", ""), "⚠️ نیاز به پیگیری"),
             f"گفتگو #{conversation_id}"]
    for key, label in (("order_id", "سفارش"), ("parcel_id", "مرسوله"),
                       ("status", "وضعیت"), ("tracking_code", "کد رهگیری")):
        if alert.get(key):
            lines.append(f"{label}: {alert[key]}")
    if alert.get("text"):
        lines.append(f"گفتهٔ مشتری: «{alert['text'][:300]}»")
    lines.append("ایجنت خودش جواب داده و در گفتگو مانده است.")
    lines.append(f"باز کردن در چت‌وود: {_conversation_link(conversation_id)}")
    text = "\n".join(lines)
    sent = 0
    for op in operators:
        try:
            await app.bot.send_message(op.telegram_chat_id, text, disable_web_page_preview=True)
            sent += 1
        except Exception:  # noqa: BLE001 — one operator's failure must not stop fanout
            log.exception("could not alert operator %s", op.chatwoot_user_id)
    return sent


async def relay_to_operator(*, operator_chat_id: int, conversation_id: int,
                            handoff_id: int, text: str) -> None:
    app = _application
    if app is None:
        return
    msg = await app.bot.send_message(
        operator_chat_id, f"💬 Customer (conv #{conversation_id}):\n{text}")
    with db_session() as session:
        session.add(TelegramMsgMap(
            telegram_chat_id=operator_chat_id, telegram_message_id=msg.message_id,
            conversation_id=conversation_id, handoff_id=handoff_id, kind="relay"))


# ---------------------------------------------------------------------------
# callbacks (claim / resolve / return)
# ---------------------------------------------------------------------------

async def _on_callback(update: Update, _context) -> None:
    from app.services import handoff as handoff_service

    query = update.callback_query
    chat_id = update.effective_chat.id
    data = query.data or ""
    with db_session() as session:
        op = _operator_by_chat(session, chat_id)
    if op is None or not op.active:
        await query.answer("Your Telegram is not linked to an active operator.",
                           show_alert=True)
        return

    if data.startswith("claim:"):
        handoff_id = int(data.split(":", 1)[1])
        info = await handoff_service.claim_and_assign(handoff_id, op.id, via="telegram")
        if info is None:
            with db_session() as session:
                handoff = session.get(Handoff, handoff_id)
                claimer = (session.get(TelegramOperator, handoff.claimed_by)
                           if handoff and handoff.claimed_by else None)
            who = claimer.chatwoot_user_name if claimer else "another operator"
            await query.answer(f"Already claimed by {who}.", show_alert=True)
            try:
                await query.edit_message_text(
                    query.message.text + f"\n\n⛔ Claimed by {who}.", reply_markup=None)
            except Exception:  # noqa: BLE001
                pass
            return
        await query.answer("Claimed — the conversation is yours.")
        console = (query.message.text
                   + f"\n\n✅ Claimed by you. Reply to customer messages here, or use the buttons.")
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ Resolve", callback_data=f"resolve:{info['conversation_id']}"),
            InlineKeyboardButton("🤖 Return to AI",
                                 callback_data=f"return:{info['conversation_id']}"),
        ]])
        try:
            await query.edit_message_text(console, reply_markup=keyboard,
                                          disable_web_page_preview=True)
            with db_session() as session:
                row = session.get(TelegramMsgMap, (chat_id, query.message.message_id))
                if row:
                    row.kind = "console"
        except Exception:  # noqa: BLE001
            log.exception("could not edit claim message")
        await _update_loser_messages(info["handoff_id"], info["operator_name"], chat_id)
        try:
            await chatwoot_client.send_message(
                info["conversation_id"],
                f"👤 Operator {info['operator_name']} claimed this conversation via Telegram.",
                private=True)
        except Exception:  # noqa: BLE001
            pass
        return

    if data.startswith(("resolve:", "return:")):
        action, conv_str = data.split(":", 1)
        conversation_id = int(conv_str)
        with db_session() as session:
            conv = crud.get_conversation(session, conversation_id)
            if conv.assignee_operator_id != op.id:
                allowed = conv.assignee_operator_id is None
            else:
                allowed = True
        if not allowed:
            await query.answer("This conversation is claimed by another operator.",
                               show_alert=True)
            return
        actor = op.chatwoot_user_name
        if action == "resolve":
            ok = await handoff_service.resolve(conversation_id, actor=actor, via="telegram")
            await query.answer("Resolved ✅" if ok else "Could not resolve in Chatwoot",
                               show_alert=not ok)
            if ok:
                try:
                    await query.edit_message_reply_markup(None)
                    await query.message.reply_text(
                        f"✅ Conversation #{conversation_id} resolved.")
                except Exception:  # noqa: BLE001
                    pass
        else:
            ok = await handoff_service.return_to_ai(conversation_id, actor=actor,
                                                    via="telegram")
            await query.answer("Returned to AI 🤖" if ok else "Could not update Chatwoot",
                               show_alert=not ok)
            if ok:
                try:
                    await query.edit_message_reply_markup(None)
                    await query.message.reply_text(
                        f"🤖 Conversation #{conversation_id} handed back to the AI.")
                except Exception:  # noqa: BLE001
                    pass


async def _update_loser_messages(handoff_id: int, winner_name: str,
                                 winner_chat_id: int) -> None:
    app = _application
    if app is None:
        return
    with db_session() as session:
        rows = list(session.scalars(select(TelegramMsgMap).where(
            TelegramMsgMap.handoff_id == handoff_id, TelegramMsgMap.kind == "notify")))
    for row in rows:
        if row.telegram_chat_id == winner_chat_id:
            continue
        try:
            await app.bot.edit_message_reply_markup(
                chat_id=row.telegram_chat_id, message_id=row.telegram_message_id,
                reply_markup=None)
            await app.bot.edit_message_text(
                chat_id=row.telegram_chat_id, message_id=row.telegram_message_id,
                text=f"⛔ Claimed by {winner_name}.")
        except Exception:  # noqa: BLE001 — message may be deleted/edited already
            log.debug("could not update notify message in chat %s", row.telegram_chat_id)


# ---------------------------------------------------------------------------
# operator replies
# ---------------------------------------------------------------------------

async def _on_message(update: Update, _context) -> None:
    message = update.effective_message
    chat_id = update.effective_chat.id
    with db_session() as session:
        op = _operator_by_chat(session, chat_id)
    if op is None or not op.active:
        await message.reply_text("Your Telegram is not linked to a Chatwoot operator. "
                                 "Link it from Chatwoot → Profile Settings.")
        return

    conversation_id = None
    if message.reply_to_message:
        with db_session() as session:
            row = session.get(TelegramMsgMap,
                              (chat_id, message.reply_to_message.message_id))
        if row:
            conversation_id = row.conversation_id
    if conversation_id is None:
        with db_session() as session:
            mine = list(session.scalars(select(Handoff).where(
                Handoff.status == "claimed", Handoff.claimed_by == op.id)))
        if len(mine) == 1:
            conversation_id = mine[0].conversation_id
        else:
            await message.reply_text(
                "Reply directly to a customer message so I know which conversation "
                "this belongs to." if mine else
                "You have no claimed conversation. Claim a handoff first.")
            return

    if message.voice:
        await message.reply_text("⚠️ Voice replies aren't supported yet — "
                                 "please type your answer.")
        return

    try:
        if message.photo or message.document:
            await _send_attachment(conversation_id, message, op.chatwoot_user_name)
        text = message.text or message.caption
        if text:
            await chatwoot_client.send_operator_message(
                conversation_id, text,
                content_attributes={"behdashtik_operator": op.chatwoot_user_name,
                                    "via": "telegram"})
        sent = await message.reply_text(f"✅ Sent to conversation #{conversation_id}.")
        with db_session() as session:
            session.add(TelegramMsgMap(
                telegram_chat_id=chat_id, telegram_message_id=sent.message_id,
                conversation_id=conversation_id, kind="relay"))
    except Exception as e:  # noqa: BLE001 — operator must see delivery failures
        log.exception("operator reply delivery failed")
        await message.reply_text(
            f"❌ Delivery to Chatwoot failed ({e.__class__.__name__}). "
            "Please retry, or answer from the dashboard: "
            f"{_conversation_link(conversation_id)}")


async def _send_attachment(conversation_id: int, message, operator_name: str) -> None:
    """Forward a Telegram photo/document to Chatwoot as a message attachment."""
    import httpx

    settings = get_settings()
    if message.photo:
        tg_file = await message.photo[-1].get_file()
        filename = "photo.jpg"
    else:
        tg_file = await message.document.get_file()
        filename = message.document.file_name or "file"
    data = bytes(await tg_file.download_as_bytearray())
    url = (f"{settings.chatwoot_base_url}/api/v1/accounts/"
           f"{settings.chatwoot_account_id}/conversations/{conversation_id}/messages")
    async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as client:
        resp = await client.post(
            url,
            headers={"api_access_token": settings.chatwoot_user_token},
            data={"message_type": "outgoing", "private": "false",
                  "content": message.caption or ""},
            files={"attachments[]": (filename, data)},
        )
        resp.raise_for_status()
