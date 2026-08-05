"""کلاینت چت‌وود: کانتکت، گفتگو و پیام روی اینباکس API باسلام."""
import json
import logging

import httpx

from . import config, store

log = logging.getLogger("bslm-bridge.chatwoot")

_client = httpx.Client(
    base_url=f"{config.CW_API}/api/v1/accounts/{config.CW_ACCOUNT_ID}",
    headers={"api_access_token": config.CW_TOKEN},
    timeout=20.0,
)


def _identifier(hash_id: str) -> str:
    return f"basalam:{hash_id}"


def find_or_create_contact(person: dict) -> int | None:
    """person همان آبجکت sender/receiver در پیلود وبهوک باسلام است."""
    hash_id = person.get("hash_id")
    if not hash_id:
        return None

    cached = store.get_contact(hash_id)
    if cached:
        return cached

    city = (person.get("city") or {}).get("name")
    province = ((person.get("city") or {}).get("province") or {}).get("name")
    avatar = (person.get("avatar") or {}).get("md") or (person.get("avatar") or {}).get("original")

    payload = {
        "identifier": _identifier(hash_id),
        "name": person.get("name") or hash_id,
        "custom_attributes": {
            "basalam_user_id": person.get("id"),
            "basalam_hash_id": hash_id,
            "basalam_city": city,
            "basalam_province": province,
        },
    }
    if avatar:
        payload["avatar_url"] = avatar

    resp = _client.post("/contacts", json=payload)
    if resp.status_code < 400:
        contact_id = resp.json()["payload"]["contact"]["id"]
    else:
        contact_id = _search_contact(hash_id)
        if not contact_id:
            log.error("contact create failed %s: %s %s", hash_id, resp.status_code, resp.text[:300])
            return None

    store.save_contact(hash_id, contact_id, person.get("id"))
    return contact_id


def basalam_user_id(contact_id: int) -> int | None:
    """شناسهٔ باسلامِ یک کانتکت. کانتکت‌های قدیمی این را در جدول محلی ندارند، پس
    یک بار از custom_attributes خود چت‌وود خوانده و ذخیره می‌شود."""
    cached = store.get_user_id(contact_id)
    if cached:
        return cached
    resp = _client.get(f"/contacts/{contact_id}")
    if resp.status_code >= 400:
        log.warning("contact %s fetch failed: %s", contact_id, resp.status_code)
        return None
    attrs = (resp.json().get("payload") or {}).get("custom_attributes") or {}
    user_id = attrs.get("basalam_user_id")
    if user_id:
        store.set_user_id(contact_id, int(user_id))
        return int(user_id)
    return None


def _search_contact(hash_id: str) -> int | None:
    resp = _client.get("/contacts/search", params={"q": _identifier(hash_id)})
    if resp.status_code >= 400:
        return None
    for contact in resp.json().get("payload", []):
        if contact.get("identifier") == _identifier(hash_id):
            return contact["id"]
    return None


def find_or_create_conversation(chat_id: int, contact_id: int) -> int | None:
    row = store.get_chat(chat_id)
    if row:
        return row["conversation_id"]

    source_id = f"basalam-chat-{chat_id}"
    resp = _client.post(
        "/conversations",
        json={
            "inbox_id": config.CW_INBOX_ID,
            "contact_id": contact_id,
            "source_id": source_id,
            "additional_attributes": {"basalam_chat_id": chat_id},
        },
    )
    if resp.status_code >= 400:
        log.error("conversation create failed chat=%s: %s %s", chat_id, resp.status_code, resp.text[:300])
        return None

    conversation_id = resp.json()["id"]
    store.save_chat(chat_id, conversation_id, contact_id, source_id)
    return conversation_id


def create_message(conversation_id: int, content: str, message_type: str,
                   source_id: str, content_attributes: dict | None = None) -> int | None:
    resp = _client.post(
        f"/conversations/{conversation_id}/messages",
        json={
            "content": content,
            "message_type": message_type,
            "source_id": source_id,
            "content_attributes": content_attributes or {},
        },
    )
    if resp.status_code >= 400:
        log.error("message create failed conv=%s: %s %s", conversation_id, resp.status_code, resp.text[:300])
        return None
    return resp.json().get("id")


def create_message_with_files(conversation_id: int, content: str, message_type: str,
                              source_id: str, files: list[tuple[str, bytes, str]],
                              content_attributes: dict | None = None) -> int | None:
    """پیام با پیوست واقعی — تصویر در چت‌وود به‌جای لینک خام نمایش داده می‌شود.

    چت‌وود پیوست را فقط از multipart می‌پذیرد، پس content_attributes به‌صورت
    JSON رشته‌ای در همان فرم می‌رود.
    """
    form = {
        "content": content,
        "message_type": message_type,
        "source_id": source_id,
        "content_attributes": json.dumps(content_attributes or {}),
    }
    payload = [("attachments[]", (name, data, mime)) for name, data, mime in files]
    resp = _client.post(f"/conversations/{conversation_id}/messages", data=form, files=payload)
    if resp.status_code >= 400:
        log.error("message+files failed conv=%s: %s %s",
                  conversation_id, resp.status_code, resp.text[:300])
        return None
    return resp.json().get("id")


def get_message_attachments(conversation_id: int, message_id: int) -> list[dict]:
    resp = _client.get(f"/conversations/{conversation_id}/messages")
    if resp.status_code >= 400:
        return []
    for message in resp.json().get("payload", []):
        if message.get("id") == message_id:
            return message.get("attachments") or []
    return []
