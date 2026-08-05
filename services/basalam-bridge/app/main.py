"""Behdashtik ↔ Basalam bridge.

باسلام → /hook          → کانتکت/گفتگو/پیام در چت‌وود
چت‌وود → /chatwoot-hook → ارسال پاسخ اپراتور یا ربات به باسلام
"""
import hashlib
import hmac
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, Header, Request, Response

from . import basalam, catalog, chatwoot, config, orders, store, translate

LOG_PATH = Path(config.DB_PATH).parent / "inbound.jsonl"

logging.basicConfig(level=config.LOG_LEVEL, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("bslm-bridge")

app = FastAPI(title="Behdashtik Basalam Bridge")

# اپراتور این را می‌نویسد تا گفتگو به ایجنت برگردد؛ فرمان است، نه پیام مشتری.
# سرویس ایجنت هم همین کلمه را می‌شناسد (chatwoot_webhook.RESUME_KEYWORD).
RESUME_KEYWORD = "ai"


def record(source: str, payload, authorized: bool) -> None:
    entry = {
        "at": datetime.now(timezone.utc).isoformat(),
        "source": source,
        "authorized": authorized,
        "payload": payload,
    }
    with LOG_PATH.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")


async def read_payload(request: Request):
    raw = await request.body()
    try:
        return raw, json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        return raw, {}


@app.on_event("startup")
async def _startup() -> None:
    catalog.warm_up()


@app.get("/health")
async def health():
    return {"ok": True, "self_user_id": config.SELF_USER_ID,
            "inbox_id": config.CW_INBOX_ID, "catalog": catalog.status()}


@app.post("/hook")
async def basalam_hook(request: Request, authorization: str = Header(default="")):
    _, payload = await read_payload(request)
    authorized = hmac.compare_digest(authorization, f"Bearer {config.WEBHOOK_SECRET}")
    record("basalam", payload, authorized)

    if not authorized:
        log.warning("basalam webhook with bad auth header")
        return Response(status_code=401)

    message_id = payload.get("id")
    chat_id = payload.get("chat_id")
    if not message_id or not chat_id:
        return {"ok": True, "skipped": "no ids"}

    # ما خودمان این پیام را از چت‌وود فرستادیم؛ برگرداندنش حلقه می‌سازد
    if store.was_sent_by_us(message_id):
        return {"ok": True, "skipped": "our own message"}

    # تحویل مجدد وبهوک نباید پیام تکراری بسازد
    if not store.claim_event(message_id):
        return {"ok": True, "skipped": "duplicate"}

    outgoing = payload.get("sender_id") == config.SELF_USER_ID
    person = payload.get("receiver") if outgoing else payload.get("sender")
    if not person:
        log.warning("payload without person, chat=%s msg=%s", chat_id, message_id)
        return {"ok": True, "skipped": "no person"}

    contact_id = chatwoot.find_or_create_contact(person)
    if not contact_id:
        return Response(status_code=500)

    conversation_id = chatwoot.find_or_create_conversation(chat_id, contact_id)
    if not conversation_id:
        return Response(status_code=500)

    content, attributes = translate.render(payload)
    direction = "outgoing" if outgoing else "incoming"
    source_id = f"{config.SOURCE_PREFIX}{message_id}"
    files = translate.downloadable_files(payload)
    if files:
        created = chatwoot.create_message_with_files(
            conversation_id, content, direction, source_id, files, attributes
        )
    else:
        created = chatwoot.create_message(
            conversation_id, content, direction, source_id, attributes
        )
    log.info(
        "basalam→chatwoot %s chat=%s msg=%s conv=%s cw_msg=%s type=%s",
        "outgoing" if outgoing else "incoming",
        chat_id, message_id, conversation_id, created, payload.get("message_type"),
    )
    return {"ok": True, "conversation_id": conversation_id, "message_id": created}


def _valid_signature(raw: bytes, timestamp: str, signature: str) -> bool:
    if not timestamp or not signature:
        return False
    expected = hmac.new(
        config.CW_WEBHOOK_SECRET.encode(),
        f"{timestamp}.".encode() + raw,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(signature, f"sha256={expected}")


@app.post("/chatwoot-hook")
async def chatwoot_hook(
    request: Request,
    x_chatwoot_timestamp: str = Header(default=""),
    x_chatwoot_signature: str = Header(default=""),
):
    raw, payload = await read_payload(request)
    authorized = _valid_signature(raw, x_chatwoot_timestamp, x_chatwoot_signature)
    record("chatwoot", payload, authorized)

    if not authorized:
        log.warning("chatwoot webhook with bad signature")
        return Response(status_code=401)

    if payload.get("event") != "message_created":
        return {"ok": True, "skipped": "event"}
    if payload.get("message_type") != "outgoing":
        return {"ok": True, "skipped": "not outgoing"}
    if payload.get("private"):
        return {"ok": True, "skipped": "private note"}
    if (payload.get("source_id") or "").startswith(config.SOURCE_PREFIX):
        return {"ok": True, "skipped": "mirrored from basalam"}

    conversation_id = (payload.get("conversation") or {}).get("id")
    content = payload.get("content") or ""
    attachments = payload.get("attachments") or []

    # فرمان اپراتور برای پس دادن گفتگو به ایجنت — پیام مشتری نیست و نباید برود.
    # باسلام حذف پیام ندارد، پس تنها فرصت جلوگیری همین‌جاست؛ خودِ حباب چت‌وود را
    # سرویس ایجنت پاک می‌کند. عمداً پیش از هر جست‌وجویی: این کلمه تحت هیچ شرایطی
    # نباید به مشتری برسد.
    if content.strip().lower() == RESUME_KEYWORD and not attachments:
        log.info("resume keyword on conv=%s — not forwarding to basalam", conversation_id)
        return {"ok": True, "skipped": "resume keyword"}

    row = store.get_chat_by_conversation(conversation_id) if conversation_id else None
    if not row:
        return {"ok": True, "skipped": "unknown conversation"}
    if not content and not attachments:
        return {"ok": True, "skipped": "empty content"}

    sent = []
    # تصویرهای اپراتور: دانلود از چت‌وود، آپلود به باسلام، ارسال به‌صورت picture
    for index, attachment in enumerate(attachments):
        url = attachment.get("data_url") or attachment.get("file_url")
        if not url:
            continue
        fetched = basalam.fetch_file(url)
        if not fetched:
            continue
        data, mime = fetched
        name = url.rsplit("/", 1)[-1].split("?")[0] or f"upload-{index}"
        uploaded = basalam.upload_file(name, data, mime)
        if not uploaded:
            continue
        # متن فقط همراه اولین تصویر می‌رود تا تکرار نشود
        sent_id = basalam.send_picture(row["chat_id"], uploaded, content if not sent else "")
        if sent_id:
            sent.append(sent_id)

    if content and not sent:
        sent_id = basalam.send_text(row["chat_id"], content)
        if sent_id:
            sent.append(sent_id)

    if not sent:
        return Response(status_code=502)

    for message_id in sent:
        store.mark_sent_to_basalam(message_id)
    log.info("chatwoot→basalam conv=%s chat=%s msgs=%s", conversation_id, row["chat_id"], sent)
    return {"ok": True, "basalam_message_ids": sent}


@app.get("/products/search")
async def products_search(q: str, limit: int = 5, authorization: str = Header(default="")):
    """قیمت و موجودی مرجع برای مشتری باسلام — از خود باسلام، نه از سایت."""
    if not hmac.compare_digest(authorization, f"Bearer {config.WEBHOOK_SECRET}"):
        return Response(status_code=401)
    return {"data": catalog.search(q, limit)}


@app.post("/products/match")
async def products_match(request: Request, authorization: str = Header(default="")):
    """عنوان محصول هاب → محصول غرفهٔ باسلام.

    جستجو در هاب انجام می‌شود (فارسی را می‌فهمد و توضیحات کامل دارد)، ولی قیمت و
    موجودیِ مشتری باسلام باید از خود باسلام بیاید. این اندپوینت پل بین آن دوست.
    """
    if not hmac.compare_digest(authorization, f"Bearer {config.WEBHOOK_SECRET}"):
        return Response(status_code=401)

    _, payload = await read_payload(request)
    matched = {}
    for title in (payload.get("titles") or [])[:24]:
        found = catalog.by_title(title)
        if found:
            matched[title] = found
    return {"data": matched}


@app.get("/products/{product_id}")
async def products_show(product_id: int, authorization: str = Header(default="")):
    if not hmac.compare_digest(authorization, f"Bearer {config.WEBHOOK_SECRET}"):
        return Response(status_code=401)
    product = basalam.get_product(product_id)
    if not product:
        return Response(status_code=404)
    return {"data": _product_brief(product)}


def _variants(product: dict) -> list[dict]:
    """مدل‌های یک آگهی (شماره، رنگ، حجم) با موجودی جداگانهٔ خودشان.

    فهرست محصولات غرفه این را ندارد؛ فقط همین اندپوینت. بدون آن، «شمارهٔ ۱ هست؟»
    را نمی‌شود درست جواب داد.
    """
    out = []
    for variant in product.get("variants") or []:
        labels = {(p.get("property") or {}).get("title", ""):
                  (p.get("value") or {}).get("title", "")
                  for p in variant.get("properties") or []}
        price = variant.get("price")
        out.append({
            "id": variant.get("id"),
            "attributes": labels,
            "price_toman": int(price) // 10 if price else None,
            "stock": variant.get("stock") or 0,
        })
    return out


def _product_brief(product: dict) -> dict:
    """همان اسکیمای آیتم کاتالوگ — مصرف‌کننده نباید دو شکل داده ببیند."""
    price, primary = product.get("price"), product.get("primary_price")
    inventory = product.get("inventory")
    return {
        "variants": _variants(product),
        # پشتیبان وقتی محصول در هاب پیدا نمی‌شود: متن باسلام کوتاه است ولی از هیچ بهتر
        "summary": (product.get("summary") or "")[:400],
        "description": (product.get("description") or "")[:1500],
        "id": product.get("id"),
        "title": product.get("title"),
        "price_toman": int(price) // 10 if price else None,
        "primary_price_toman": int(primary) // 10 if primary else None,
        "inventory": inventory,
        # `is_available` باسلام برای آگهیِ تمام‌شده هم True است (موجودی صفر، در دسترس)،
        # پس همان قاعدهٔ کاتالوگ ملاک است: منتشرشده + فروختنی + موجودی واقعی
        "available": (product.get("status") or {}).get("value") == catalog.AVAILABLE_STATUS
                     and bool(product.get("is_saleable"))
                     and (inventory or 0) > 0,
        "vendor_id": (product.get("vendor") or {}).get("id"),
        # اجتماعیِ خرید: نظر و امتیاز فقط سمت باسلام وجود دارد و همان چیزی است که
        # مشتری روی همین آگهی می‌بیند — پس مدرک قابل‌ارجاع است، نه ادعای ما
        "rating": product.get("rating"),
        "review_count": product.get("review_count"),
        "photo": (product.get("photo") or {}).get("md") if isinstance(product.get("photo"), dict)
                 else product.get("photo"),
    }


@app.get("/orders")
async def orders_of_conversation(conversation_id: int, limit: int = 3,
                                 authorization: str = Header(default="")):
    """سفارش‌های همان کسی که در این گفتگو حرف می‌زند.

    هویت از خودِ گفتگو می‌آید (chat → contact → شناسهٔ باسلام)، پس نه شمارهٔ
    سفارش از مشتری پرسیده می‌شود و نه شمارهٔ موبایل.
    """
    if not hmac.compare_digest(authorization, f"Bearer {config.WEBHOOK_SECRET}"):
        return Response(status_code=401)

    row = store.get_chat_by_conversation(conversation_id)
    if not row:
        return {"data": [], "reason": "unknown conversation"}
    user_id = chatwoot.basalam_user_id(row["contact_id"])
    if not user_id:
        return {"data": [], "reason": "no basalam user id"}
    try:
        return {"data": orders.for_customer(user_id, limit=limit), "customer_id": user_id}
    except Exception:  # noqa: BLE001 — بالادست باید بین «نداریم» و «نشد» فرق بگذارد
        log.exception("orders lookup failed for conversation %s", conversation_id)
        return Response(status_code=502)


@app.post("/outgoing/review")
async def outgoing_review(request: Request, authorization: str = Header(default="")):
    """کارت «ثبت تجربهٔ خرید» برای اقلام یک سفارش تحویل‌شده."""
    if not hmac.compare_digest(authorization, f"Bearer {config.WEBHOOK_SECRET}"):
        return Response(status_code=401)

    _, payload = await read_payload(request)
    conversation_id = payload.get("conversation_id")
    item_ids = payload.get("item_ids") or []
    row = store.get_chat_by_conversation(conversation_id) if conversation_id else None
    if not row:
        return Response(status_code=404)

    sent, skipped = [], 0
    for item_id in item_ids[:6]:
        if not store.claim_review(int(item_id)):
            skipped += 1
            continue
        sent_id = basalam.send_review(row["chat_id"], int(item_id))
        if sent_id:
            store.mark_sent_to_basalam(sent_id)
            sent.append(sent_id)
    log.info("review cards conv=%s chat=%s → %s (skipped %s already sent)",
             conversation_id, row["chat_id"], sent, skipped)
    if not sent and not skipped:
        return Response(status_code=502)
    return {"ok": True, "basalam_message_ids": sent, "already_sent": skipped}


@app.post("/outgoing/product")
async def outgoing_product(request: Request, authorization: str = Header(default="")):
    """کارت محصول باسلام؛ ایجنت باسلام این را صدا می‌زند.

    کارت هم به مشتری می‌رود و هم در چت‌وود آینه می‌شود تا اپراتور ببیند چه رفته.
    """
    if not hmac.compare_digest(authorization, f"Bearer {config.WEBHOOK_SECRET}"):
        return Response(status_code=401)

    _, payload = await read_payload(request)
    conversation_id = payload.get("conversation_id")
    product_ids = payload.get("product_ids") or []
    row = store.get_chat_by_conversation(conversation_id) if conversation_id else None
    if not row:
        return Response(status_code=404)

    sent = []
    for product_id in product_ids[:4]:
        sent_id = basalam.send_product(row["chat_id"], int(product_id))
        if not sent_id:
            continue
        store.mark_sent_to_basalam(sent_id)
        sent.append(sent_id)
        content, attributes = translate.render(
            {"message": {"entity_id": int(product_id)}, "message_type": "PRODUCT"}
        )
        # `bot-` marks it as ours: the agent treats every other mirrored outgoing
        # message as an operator typing in the Basalam panel and then keeps quiet.
        chatwoot.create_message(
            conversation_id, content, "outgoing",
            f"{config.SOURCE_PREFIX}bot-{sent_id}", attributes,
        )

    log.info("product cards conv=%s chat=%s → %s", conversation_id, row["chat_id"], sent)
    return {"ok": True, "basalam_message_ids": sent}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=config.HOST, port=config.PORT)
