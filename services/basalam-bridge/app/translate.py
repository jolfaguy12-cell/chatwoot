"""ترجمهٔ پیام باسلام به متن و پیوست قابل نمایش در چت‌وود."""
from . import basalam, config

FILE_KINDS = {"PICTURE", "GALLERY", "FILE", "VOICE", "VIDEO"}
FILE_LABELS = {"PICTURE": "🖼 تصویر", "GALLERY": "🖼 تصاویر", "FILE": "📎 فایل",
               "VOICE": "🎤 پیام صوتی", "VIDEO": "🎬 ویدیو"}


def _toman(rial) -> str:
    """قیمت‌های باسلام ریالی هستند؛ برای نمایش به تومان تبدیل می‌شوند."""
    try:
        return f"{int(rial) // 10:,} تومان"
    except (TypeError, ValueError):
        return "—"


def _product_block(product_id: int) -> str:
    product = basalam.get_product(product_id)
    url = config.PRODUCT_URL.format(id=product_id)
    if not product:
        return f"🛍 محصول #{product_id}\n{url}"

    lines = [f"🛍 {product.get('title') or f'محصول #{product_id}'}"]

    price, primary = product.get("price"), product.get("primary_price")
    if price and primary and primary != price:
        lines.append(f"💰 {_toman(price)}  (قیمت اصلی {_toman(primary)})")
    elif price:
        lines.append(f"💰 {_toman(price)}")

    lines.append("✅ موجود" if product.get("is_available") else "⛔️ ناموجود")

    vendor = product.get("vendor") or {}
    if vendor.get("id") and vendor["id"] != config.VENDOR_ID:
        lines.append(f"⚠️ از غرفهٔ «{vendor.get('title')}» — محصول ما نیست")

    lines.append(url)
    return "\n".join(lines)


def render(payload: dict) -> tuple[str, dict]:
    """(متن پیام, content_attributes) را برمی‌گرداند."""
    message = payload.get("message") or {}
    kind = (payload.get("message_type") or "TEXT").upper()
    text = message.get("text") or ""
    entity_id = message.get("entity_id")
    attributes: dict = {"basalam_message_type": kind}

    parts = []

    if kind == "PRODUCT" and entity_id:
        parts.append(_product_block(entity_id))
        attributes["basalam_product_id"] = entity_id
    elif kind == "VENDOR" and entity_id:
        parts.append(f"🏪 غرفه #{entity_id}\nhttps://basalam.com/shop/{entity_id}")
        attributes["basalam_vendor_id"] = entity_id
    elif kind in FILE_KINDS:
        parts.append(FILE_LABELS[kind])
    elif kind == "LOCATION":
        parts.append("📍 موقعیت مکانی ارسال شد")
    elif kind != "TEXT":
        parts.append(f"[پیام از نوع {kind}]")

    if text:
        parts.append(text)

    return "\n\n".join(p for p in parts if p) or f"[پیام از نوع {kind}]", attributes


def downloadable_files(payload: dict) -> list[tuple[str, bytes, str]]:
    """فایل‌های پیام را دانلود می‌کند تا پیوست واقعی چت‌وود شوند."""
    kind = (payload.get("message_type") or "").upper()
    if kind not in FILE_KINDS:
        return []

    files = (payload.get("message") or {}).get("files") or []
    downloaded = []
    for index, item in enumerate(files):
        url = item.get("url")
        if not url:
            continue
        fetched = basalam.fetch_file(url)
        if not fetched:
            continue
        data, mime = fetched
        name = item.get("name") or url.rsplit("/", 1)[-1].split("?")[0] or f"file-{index}"
        downloaded.append((name, data, mime))
    return downloaded
