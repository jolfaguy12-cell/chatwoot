"""کلاینت باسلام: ارسال پیام (متن/تصویر/کارت محصول)، آپلود فایل و خواندن محصول."""
import logging

import httpx

from . import config

log = logging.getLogger("bslm-bridge.basalam")

_client = httpx.Client(
    base_url=config.BSLM_API,
    headers={"Authorization": f"Bearer {config.BSLM_TOKEN}"},
    timeout=30.0,
)

_product_cache: dict[int, dict] = {}


def _send(chat_id: int, body: dict) -> int | None:
    resp = _client.post(f"/v1/chats/{chat_id}/messages", json=body)
    if resp.status_code >= 400:
        log.error("basalam send failed chat=%s status=%s body=%s",
                  chat_id, resp.status_code, resp.text[:300])
        return None
    return (resp.json().get("data") or {}).get("id")


def send_text(chat_id: int, text: str) -> int | None:
    return _send(chat_id, {"chat_id": chat_id, "message_type": "text",
                           "content": {"text": text}})


def send_product(chat_id: int, product_id: int, text: str = "") -> int | None:
    """کارت واقعی محصول باسلام — نه لینک."""
    body = {"chat_id": chat_id, "message_type": "product",
            "content": {"entity_id": product_id}}
    if text:
        body["content"]["text"] = text
    return _send(chat_id, body)


def send_review(chat_id: int, order_item_id: int) -> int | None:
    """کارت «ثبت تجربهٔ خرید» — entity_id شناسهٔ قلمِ سفارش است، نه محصول."""
    return _send(chat_id, {"chat_id": chat_id, "message_type": "review",
                           "content": {"entity_id": order_item_id}})


def upload_file(name: str, data: bytes, content_type: str) -> dict | None:
    """فایل را روی سرویس آپلود باسلام می‌گذارد و رکورد فایل را برمی‌گرداند."""
    resp = _client.post(
        "/v1/files",
        files={"file": (name, data, content_type)},
        data={"file_type": "chat.photo"},
    )
    if resp.status_code >= 400:
        log.error("basalam upload failed %s: %s", resp.status_code, resp.text[:300])
        return None
    return resp.json()


def send_picture(chat_id: int, file_record: dict, text: str = "") -> int | None:
    attachment = {"files": [{
        "id": file_record.get("id"),
        "url": file_record.get("url") or file_record.get("urls", {}).get("original"),
        "width": file_record.get("width"),
        "height": file_record.get("height"),
        "name": file_record.get("name"),
        "type": file_record.get("type"),
        "size": file_record.get("size"),
    }]}
    body = {"chat_id": chat_id, "message_type": "picture", "attachment": attachment}
    if text:
        body["content"] = {"text": text}
    return _send(chat_id, body)


def fetch_file(url: str) -> tuple[bytes, str] | None:
    """فایل باسلام را دانلود می‌کند تا به‌عنوان پیوست واقعی در چت‌وود بنشیند."""
    try:
        resp = httpx.get(url, timeout=30.0, follow_redirects=True)
    except httpx.HTTPError as exc:
        log.warning("file download failed %s: %s", url[:80], exc)
        return None
    if resp.status_code >= 400:
        log.warning("file download %s -> %s", url[:80], resp.status_code)
        return None
    return resp.content, resp.headers.get("content-type", "application/octet-stream")


def get_product(product_id: int) -> dict | None:
    if product_id in _product_cache:
        return _product_cache[product_id]

    resp = _client.get(f"/v1/products/{product_id}")
    if resp.status_code >= 400:
        log.warning("product %s fetch failed: %s", product_id, resp.status_code)
        return None

    product = resp.json()
    _product_cache[product_id] = product
    return product


def search_products(title: str, per_page: int = 5) -> list[dict]:
    """جستجو در محصولات غرفهٔ خودمان — مرجع قیمت و موجودی برای مشتری باسلام."""
    resp = _client.get(f"/v1/vendors/{config.VENDOR_ID}/products",
                       params={"title": title, "per_page": per_page})
    if resp.status_code >= 400:
        log.warning("vendor product search failed: %s", resp.status_code)
        return []
    return resp.json().get("data") or []
