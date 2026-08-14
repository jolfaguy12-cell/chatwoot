"""Chatwoot API client.

Two credentials:
- bot token   → replies, status toggles, assignment, custom attributes
                (the only endpoints the AgentBot allowlist permits)
- user token  → message-history reads and attachment downloads (the bot
                allowlist has no read endpoints)
"""

import logging

import httpx

from app.config import get_settings

log = logging.getLogger(__name__)

_TRANSPORT_RETRIES = 3


class ChatwootError(Exception):
    pass


# One client per token, kept alive for the process: a fresh AsyncClient per call
# costs a TLS handshake, and a turn makes half a dozen of these calls.
_clients: dict[str, httpx.AsyncClient] = {}


def _client(token: str) -> httpx.AsyncClient:
    client = _clients.get(token)
    if client is None or client.is_closed:
        settings = get_settings()
        client = httpx.AsyncClient(
            base_url=f"{settings.chatwoot_base_url}/api/v1/accounts/{settings.chatwoot_account_id}",
            headers={"api_access_token": token},
            timeout=httpx.Timeout(15.0),
            transport=httpx.AsyncHTTPTransport(retries=_TRANSPORT_RETRIES),
        )
        _clients[token] = client
    return client


async def aclose() -> None:
    for client in list(_clients.values()):
        await client.aclose()
    _clients.clear()


async def _request(token: str, method: str, path: str, **kwargs) -> dict:
    resp = await _client(token).request(method, path, **kwargs)
    if resp.status_code >= 400:
        raise ChatwootError(f"{method} {path} -> {resp.status_code}: {resp.text[:300]}")
    if resp.content and resp.headers.get("content-type", "").startswith("application/json"):
        try:
            return resp.json()
        except ValueError:
            return {}
    return {}


# در باسلام همهٔ پیام‌های غرفه یک‌شکل‌اند و مشتری نمی‌داند با ایجنت حرف می‌زند یا
# با همکار انسانی. هر پیامی که از این سرویس برای مشتری می‌رود این امضا را دارد.
SIGNATURE = "دستیار هوش مصنوعی بهداشتیک"


# --- bot-token actions -----------------------------------------------------

async def send_message(conversation_id: int, content: str, *, private: bool = False,
                       content_attributes: dict | None = None,
                       content_type: str | None = None) -> dict:
    # امضا اینجا زده می‌شود، نه در متن پاسخ: تنها گلوگاهی که هر پیامِ عمومیِ این
    # سرویس از آن رد می‌شود، پس مسیر تازه‌ای هم که بعداً اضافه شود امضا را جا
    # نمی‌اندازد. یادداشت‌های خصوصی (private) پیامِ مشتری نیستند و امضا نمی‌گیرند.
    if not private and content and not content.rstrip().endswith(SIGNATURE):
        content = f"{content.rstrip()}\n\n{SIGNATURE}"
    payload: dict = {"content": content, "message_type": "outgoing", "private": private}
    if content_attributes:
        payload["content_attributes"] = content_attributes
    if content_type:
        payload["content_type"] = content_type
    return await _request(
        get_settings().chatwoot_bot_token, "POST",
        f"/conversations/{conversation_id}/messages", json=payload,
    )


async def send_product_cards(conversation_id: int, cards: list[dict]) -> dict:
    """Native Chatwoot `cards` message — the widget renders it with its own
    ChatCard component (image, price, action buttons); no styling of ours."""
    return await send_message(
        conversation_id, "محصولات پیشنهادی", content_type="cards",
        content_attributes={"items": cards},
    )


async def toggle_status(conversation_id: int, status: str) -> dict:
    return await _request(
        get_settings().chatwoot_bot_token, "POST",
        f"/conversations/{conversation_id}/toggle_status", json={"status": status},
    )


async def assign_agent(conversation_id: int, assignee_id: int) -> dict:
    return await _request(
        get_settings().chatwoot_bot_token, "POST",
        f"/conversations/{conversation_id}/assignments", json={"assignee_id": assignee_id},
    )


async def toggle_typing(conversation_id: int, status: str = "on") -> None:
    try:
        await _request(
            get_settings().chatwoot_bot_token, "POST",
            f"/conversations/{conversation_id}/toggle_typing_status",
            params={"typing_status": status},
        )
    except ChatwootError:  # cosmetic — never fail a run over typing status
        pass


async def get_conversation(conversation_id: int) -> dict:
    return await _request(
        get_settings().chatwoot_bot_token, "GET", f"/conversations/{conversation_id}"
    )


async def add_labels(conversation_id: int, labels: list[str]) -> None:
    """Chatwoot's labels endpoint replaces the whole list, so add to what is there."""
    current = await _request(
        get_settings().chatwoot_user_token, "GET",
        f"/conversations/{conversation_id}/labels",
    )
    merged = list(dict.fromkeys((current.get("payload") or []) + labels))
    await _request(
        get_settings().chatwoot_user_token, "POST",
        f"/conversations/{conversation_id}/labels", json={"labels": merged},
    )


async def delete_message(conversation_id: int, message_id: int) -> bool:
    """Remove a bubble from the thread. Used for the operator's resume keyword,
    which is a command to us, not something the customer should read."""
    try:
        await _request(
            get_settings().chatwoot_user_token, "DELETE",
            f"/conversations/{conversation_id}/messages/{message_id}",
        )
    except Exception:  # noqa: BLE001 — a stray bubble must not block the resume
        log.exception("could not delete message %s in conversation %s",
                      message_id, conversation_id)
        return False
    return True


# --- user-token reads ------------------------------------------------------

async def get_messages(conversation_id: int) -> list[dict]:
    data = await _request(
        get_settings().chatwoot_user_token, "GET",
        f"/conversations/{conversation_id}/messages",
    )
    return data.get("payload", [])


async def send_operator_message(conversation_id: int, content: str,
                                content_attributes: dict | None = None) -> dict:
    """Public reply on behalf of a human operator (sent with the user token so
    it renders under the AI service's operator account, not the bot)."""
    payload: dict = {"content": content, "message_type": "outgoing", "private": False}
    if content_attributes:
        payload["content_attributes"] = content_attributes
    return await _request(
        get_settings().chatwoot_user_token, "POST",
        f"/conversations/{conversation_id}/messages", json=payload,
    )


async def download_attachment(url: str) -> bytes:
    """Follow ActiveStorage signed redirects promptly; absolute URL comes from
    the webhook payload (FRONTEND_URL-based)."""
    async with httpx.AsyncClient(timeout=httpx.Timeout(30.0), follow_redirects=True) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        return resp.content
