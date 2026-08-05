"""دسترسی به باسلام از طریق پل (bdsk-basalam-bridge).

توکن باسلام فقط در پل نگهداری می‌شود؛ این سرویس تنها مصرف‌کننده است.
قیمت و موجودی برای مشتری باسلام **باید** از اینجا بیاید، نه از هاب — قیمت
غرفه با قیمت سایت یکی نیست.
"""
import logging

import httpx

from app.config import get_settings

log = logging.getLogger(__name__)

_client: httpx.AsyncClient | None = None


def _http() -> httpx.AsyncClient:
    global _client
    settings = get_settings()
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(
            base_url=settings.bridge_base_url,
            headers={"Authorization": f"Bearer {settings.bridge_secret}"},
            timeout=httpx.Timeout(15.0),
            transport=httpx.AsyncHTTPTransport(retries=2),
        )
    return _client


async def aclose() -> None:
    if _client is not None and not _client.is_closed:
        await _client.aclose()


class CatalogUnavailable(Exception):
    """پل یا کاتالوگ غرفه جواب نداد.

    این با «محصول در غرفه نیست» یکی نیست و هرگز نباید با آن اشتباه شود: یک بار
    تایم‌اوتِ همین درخواست باعث شد به مشتری بگوییم کرم پودر نداریم، در حالی که
    سه مدلش موجود بود.
    """


async def match_titles(titles: list[str]) -> dict[str, dict]:
    """عنوان محصول هاب → محصول غرفهٔ باسلام (id، قیمت تومان، موجودی)."""
    if not titles:
        return {}
    try:
        resp = await _http().post("/products/match", json={"titles": titles})
        resp.raise_for_status()
    except httpx.HTTPError as exc:
        log.warning("bridge match failed: %r", exc)
        raise CatalogUnavailable(str(exc) or exc.__class__.__name__) from exc
    return resp.json().get("data") or {}


async def search(query: str, limit: int = 5) -> list[dict]:
    """جستجوی مستقیم در کاتالوگ غرفه — وقتی هاب چیزی پیدا نکرد."""
    try:
        resp = await _http().get("/products/search", params={"q": query, "limit": limit})
        resp.raise_for_status()
    except httpx.HTTPError as exc:
        log.warning("bridge search failed: %s", exc)
        return []
    return resp.json().get("data") or []


async def product(product_id: int) -> dict | None:
    try:
        resp = await _http().get(f"/products/{product_id}")
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
    except httpx.HTTPError as exc:
        log.warning("bridge product failed: %s", exc)
        return None
    return resp.json().get("data")


async def orders(conversation_id: int, limit: int = 3) -> list[dict]:
    """سفارش‌های همین مشتری، تازه‌ترین اول. لیست خالی یعنی سفارشی ندارد؛
    خطای پل به‌صورت CatalogUnavailable بالا می‌رود تا با «سفارشی نداری» یکی
    گرفته نشود."""
    try:
        resp = await _http().get("/orders", params={"conversation_id": conversation_id,
                                                    "limit": limit})
        resp.raise_for_status()
    except httpx.HTTPError as exc:
        log.warning("bridge orders failed: %r", exc)
        raise CatalogUnavailable(str(exc) or exc.__class__.__name__) from exc
    return resp.json().get("data") or []


async def send_review_cards(conversation_id: int, item_ids: list[int]) -> dict:
    """کارت «ثبت تجربهٔ خرید» باسلام برای اقلام یک سفارش تحویل‌شده.

    پل تکراری‌ها را خودش کنار می‌گذارد، پس `already_sent` هم موفقیت است — یعنی
    مشتری کارت را قبلاً گرفته و نباید دوباره بگیرد.
    """
    try:
        resp = await _http().post(
            "/outgoing/review",
            json={"conversation_id": conversation_id, "item_ids": item_ids},
        )
        resp.raise_for_status()
    except httpx.HTTPError as exc:
        log.warning("bridge review cards failed: %s", exc)
        return {}
    return resp.json()


async def send_product_cards(conversation_id: int, product_ids: list[int]) -> list[int]:
    """کارت واقعی محصول باسلام برای مشتری؛ در چت‌وود هم آینه می‌شود."""
    try:
        resp = await _http().post(
            "/outgoing/product",
            json={"conversation_id": conversation_id, "product_ids": product_ids},
        )
        resp.raise_for_status()
    except httpx.HTTPError as exc:
        log.warning("bridge product cards failed: %s", exc)
        return []
    return resp.json().get("basalam_message_ids") or []
