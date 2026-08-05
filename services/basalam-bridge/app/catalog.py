"""کاتالوگ غرفه در حافظه.

جستجوی خود باسلام روی عنوان ضعیف است («بیوکسین» چیزی برنمی‌گرداند ولی «Forte»
برمی‌گرداند)، پس کل محصولات غرفه یک‌بار خوانده و محلی جستجو می‌شود.

مهم: کاتالوگ **ناقص** بدتر از کاتالوگ قدیمی است — به مشتری می‌گوییم محصولی که
داریم را نداریم. پس بارگذاری یا کامل انجام می‌شود یا نسخهٔ قبلی سر جایش می‌ماند.
"""
import logging
import re
import threading
import time

from . import basalam, config

log = logging.getLogger("bslm-bridge.catalog")

REFRESH_SECONDS = 900
PAGE_SIZE = 50
MAX_PAGES = 30
PAGE_ATTEMPTS = 3
AVAILABLE_STATUS = 2976

_lock = threading.Lock()
_items: list[dict] = []
_loaded_at = 0.0
_last_error = ""
_refreshing = False

_TRANSLATE = str.maketrans({"ي": "ی", "ك": "ک", "ؤ": "و", "إ": "ا", "أ": "ا", "ة": "ه",
                            "‌": " ",
                            "۰": "0", "۱": "1", "۲": "2", "۳": "3", "۴": "4",
                            "۵": "5", "۶": "6", "۷": "7", "۸": "8", "۹": "9"})


def normalize(text: str) -> str:
    text = (text or "").translate(_TRANSLATE).lower()
    text = re.sub(r"[^\w؀-ۿ]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _to_item(product: dict) -> dict:
    return {
        "id": product.get("id"),
        "title": product.get("title"),
        "norm": normalize(product.get("title")),
        "price_toman": (product.get("price") or 0) // 10 or None,
        "primary_price_toman": (product.get("primary_price") or 0) // 10 or None,
        "inventory": product.get("inventory"),
        "available": (product.get("status") or {}).get("value") == AVAILABLE_STATUS
                     and (product.get("inventory") or 0) > 0,
        "photo": product.get("photo"),
    }


def _fetch_page(page: int) -> dict | None:
    """صفحه را با چند تلاش می‌گیرد؛ None یعنی شکست قطعی."""
    for attempt in range(PAGE_ATTEMPTS):
        try:
            resp = basalam._client.get(
                f"/v1/vendors/{config.VENDOR_ID}/products",
                params={"per_page": PAGE_SIZE, "page": page},
            )
        except Exception as exc:  # noqa: BLE001 — هر خطای شبکه‌ای باید تلاش دوباره بگیرد
            log.warning("catalog page %s attempt %s failed: %s", page, attempt + 1, exc)
            time.sleep(1 + attempt)
            continue
        if resp.status_code < 400:
            return resp.json()
        log.warning("catalog page %s attempt %s -> %s", page, attempt + 1, resp.status_code)
        time.sleep(1 + attempt)
    return None


def _fetch_all() -> list[dict] | None:
    """کل کاتالوگ، یا None اگر ناقص ماند."""
    first = _fetch_page(1)
    if first is None:
        return None

    expected = first.get("total_count") or 0
    items = [_to_item(p) for p in (first.get("data") or [])]

    page = 2
    while len(items) < expected and page <= MAX_PAGES:
        payload = _fetch_page(page)
        if payload is None:
            return None
        batch = payload.get("data") or []
        if not batch:
            break
        items.extend(_to_item(p) for p in batch)
        page += 1

    if expected and len(items) < expected:
        log.error("catalog incomplete: %s of %s — نسخهٔ قبلی نگه داشته شد", len(items), expected)
        return None
    return items


def _load() -> bool:
    """واکشی کامل و جایگزینی اتمی. قفل فقط برای نوشتن گرفته می‌شود، نه در طول
    ۹ صفحه درخواست شبکه‌ای."""
    global _items, _loaded_at, _last_error, _refreshing
    try:
        fetched = _fetch_all()
        with _lock:
            if fetched is None:
                _last_error = "fetch failed"
                # کاتالوگ قبلی (حتی کهنه) بهتر از هیچ است
                return bool(_items)
            _items, _loaded_at, _last_error = fetched, time.time(), ""
        log.info("catalog loaded: %s products", len(fetched))
        return True
    finally:
        with _lock:
            _refreshing = False


def refresh(force: bool = False) -> bool:
    """کاتالوگ کهنه هرگز درخواست را بلاک نمی‌کند.

    واکشی کامل ~۱۶ ثانیه طول می‌کشد. وقتی این کار داخل خودِ درخواست انجام می‌شد،
    /products/match ایجنت تایم‌اوت خورد و مشتری شنید «کرم پودر نداریم» در حالی که
    سه مدلش موجود بود. حالا نسخهٔ کهنه سرو می‌شود و تازه‌سازی در پس‌زمینه انجام
    می‌گیرد؛ فقط استارت سرد (وقتی هیچ نسخه‌ای نداریم) منتظر می‌ماند.
    """
    global _refreshing
    with _lock:
        have = bool(_items)
        if have and time.time() - _loaded_at <= REFRESH_SECONDS and not force:
            return True
        if _refreshing:
            return have
        _refreshing = True
    if have:
        threading.Thread(target=_load, daemon=True).start()
        return True
    return _load()


def all_items() -> list[dict]:
    refresh()
    return _items


def status() -> dict:
    return {
        "products": len(_items),
        "age_seconds": int(time.time() - _loaded_at) if _loaded_at else None,
        "last_error": _last_error,
        "refreshing": _refreshing,
    }


def search(query: str, limit: int = 5) -> list[dict]:
    tokens = [t for t in normalize(query).split() if len(t) > 1]
    if not tokens:
        return []

    items = all_items()
    normalized_query = normalize(query)
    scored = []
    for item in items:
        hits = sum(1 for t in tokens if t in item["norm"])
        if not hits:
            continue
        # تطابق کامل عبارت امتیاز بیشتری می‌گیرد تا نتیجهٔ دقیق بالا بیاید
        score = hits * 10 + (5 if normalized_query in item["norm"] else 0)
        score += 3 if item["available"] else 0
        scored.append((score, item))

    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [item for _, item in scored[:limit]]


def by_title(title: str) -> dict | None:
    """نزدیک‌ترین محصول غرفه به عنوان هاب — پل بین کاتالوگ سایت و باسلام."""
    results = search(title, limit=1)
    return results[0] if results else None


def warm_up() -> None:
    """در پس‌زمینه هنگام بالا آمدن سرویس، تا اولین مشتری منتظر نماند."""
    threading.Thread(target=refresh, kwargs={"force": True}, daemon=True).start()
