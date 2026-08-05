"""بازآوری گفتگوهای موجود باسلام به چت‌وود.

    python -m scripts.import_chat all [تعداد پیام هر گفتگو]
    python -m scripts.import_chat <chat_id> [تعداد پیام]

زمان‌های باسلام تهران (UTC+3:30) هستند و به‌صورت external_created_at
یونیکس ذخیره می‌شوند؛ اسکریپت رفع‌وصله در deploy/ آن را روی created_at می‌نشاند.
"""
import sys
from datetime import datetime, timedelta, timezone

from app import basalam, chatwoot, config, store, translate

TEHRAN = timezone(timedelta(hours=3, minutes=30))


def to_unix(stamp: str | None) -> int | None:
    if not stamp:
        return None
    try:
        naive = datetime.strptime(stamp, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None
    return int(naive.replace(tzinfo=TEHRAN).timestamp())


def person_from_contact(contact: dict) -> dict:
    return {
        "id": contact.get("id"),
        "hash_id": contact.get("hash_id"),
        "name": contact.get("name"),
        "avatar": {"md": contact["avatar"]} if contact.get("avatar") else None,
        "city": None,
    }


def import_chat(chat: dict, limit: int) -> int:
    chat_id = chat["id"]
    contact = chat.get("contact")
    if not contact or not contact.get("hash_id"):
        print(f"  ⨯ گفتگوی {chat_id} کانتکت ندارد (گروه/کانال؟) — رد شد")
        return 0

    contact_id = chatwoot.find_or_create_contact(person_from_contact(contact))
    if not contact_id:
        print(f"  ⨯ ساخت کانتکت برای {chat_id} ناموفق")
        return 0

    conversation_id = chatwoot.find_or_create_conversation(chat_id, contact_id)
    if not conversation_id:
        print(f"  ⨯ ساخت گفتگو برای {chat_id} ناموفق")
        return 0

    response = basalam._client.get(
        f"/v1/chats/{chat_id}/messages", params={"limit": limit, "order": "desc"}
    )
    if response.status_code >= 400:
        print(f"  ⨯ خواندن پیام‌های {chat_id}: {response.status_code}")
        return 0

    imported = 0
    for message in reversed(response.json()["data"]["messages"]):
        # پیامی که خودمان از چت‌وود فرستادیم همان‌جا ثبت شده؛ دوباره نساز
        if store.was_sent_by_us(message["id"]):
            continue
        if not store.claim_event(message["id"]):
            continue
        outgoing = str(message["sender"]["id"]) == str(config.SELF_USER_ID)
        content, attributes = translate.render(
            {"message": message.get("content"), "message_type": message.get("message_type")}
        )
        attributes["external_created_at"] = to_unix(message.get("created_at"))
        chatwoot.create_message(
            conversation_id,
            content,
            "outgoing" if outgoing else "incoming",
            f"{config.SOURCE_PREFIX}{message['id']}",
            attributes,
        )
        imported += 1

    print(f"  ✓ {contact.get('name')} (chat={chat_id}) → conv={conversation_id}، {imported} پیام")
    return imported


def main() -> None:
    target = sys.argv[1]
    limit = int(sys.argv[2]) if len(sys.argv) > 2 else 30

    chats = basalam._client.get("/v1/chats", params={"limit": 100}).json()["data"]["chats"]
    if target != "all":
        chats = [c for c in chats if c["id"] == int(target)]
        if not chats:
            sys.exit(f"گفتگوی {target} پیدا نشد")

    total = 0
    for chat in chats:
        total += import_chat(chat, limit)
    print(f"\nمجموع: {len(chats)} گفتگو، {total} پیام تازه")


if __name__ == "__main__":
    main()
