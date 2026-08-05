"""Behdashtik Hub agent-API client with a small TTL cache (invalidated by Hub
webhooks). All product/order/policy data the agent uses comes through here."""

import contextvars
import logging
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import delete

from app.config import get_settings
from app.db import db_session
from app.models import HubCache

log = logging.getLogger(__name__)

PRODUCT_TTL = timedelta(minutes=10)
PAGE_TTL = timedelta(hours=6)

# Which store this run talks to. Set once per graph run from the conversation's
# inbox; everything below (requests and cache keys) follows it.
_hub = contextvars.ContextVar("hub", default=None)


def use_inbox(inbox_id: int | None) -> None:
    _hub.set(get_settings().hub_for_inbox(inbox_id))


def _current() -> tuple[str, str, str]:
    return _hub.get() or get_settings().hub_for_inbox(None)


class HubUnavailable(Exception):
    pass


# One client per hub, kept alive for the process — building a fresh AsyncClient
# per call spends ~120ms on the TLS handshake before the hub does any work.
_clients: dict[str, httpx.AsyncClient] = {}


def _client(base_url: str, api_key: str) -> httpx.AsyncClient:
    client = _clients.get(base_url)
    if client is None or client.is_closed:
        client = httpx.AsyncClient(
            base_url=base_url,
            headers={"X-Agent-API-Key": api_key},
            timeout=httpx.Timeout(10.0),
            transport=httpx.AsyncHTTPTransport(retries=2),
        )
        _clients[base_url] = client
    return client


async def aclose() -> None:
    for client in list(_clients.values()):
        await client.aclose()
    _clients.clear()


async def _get(path: str, params: dict | None = None) -> dict:
    _, base_url, api_key = _current()
    try:
        resp = await _client(base_url, api_key).get(f"/api/agent/v1{path}", params=params)
    except httpx.HTTPError as e:
        raise HubUnavailable(f"hub request failed: {e.__class__.__name__}") from e
    if resp.status_code == 404:
        return {"data": None, "not_found": True}
    if resp.status_code >= 400:
        raise HubUnavailable(f"hub {path} -> {resp.status_code}")
    return resp.json()


def _cache_get(key: str):
    key = f"{_current()[0]}:{key}"
    with db_session() as session:
        row = session.get(HubCache, key)
        if row and row.expires_at.replace(tzinfo=timezone.utc) > datetime.now(timezone.utc):
            return row.value
    return None


def _cache_set(key: str, value: dict, ttl: timedelta) -> None:
    key = f"{_current()[0]}:{key}"
    with db_session() as session:
        row = session.get(HubCache, key)
        expires = datetime.now(timezone.utc) + ttl
        if row:
            row.value = value
            row.expires_at = expires
        else:
            session.add(HubCache(key=key, value=value, expires_at=expires))


def invalidate(prefix: str) -> None:
    """Hub webhooks do not say which store fired them — drop the prefix in both."""
    with db_session() as session:
        session.execute(delete(HubCache).where(HubCache.key.like(f"%{prefix}%")))


# --- products --------------------------------------------------------------

async def product_by_slug(slug: str) -> dict | None:
    key = f"product:slug:{slug}"
    cached = _cache_get(key)
    if cached is not None:
        return cached.get("data")
    resp = await _get(f"/products/by-slug/{slug}")
    _cache_set(key, resp, PRODUCT_TTL)
    return resp.get("data")


async def product_by_id(product_id: int) -> dict | None:
    key = f"product:id:{product_id}"
    cached = _cache_get(key)
    if cached is not None:
        return cached.get("data")
    resp = await _get(f"/products/{product_id}")
    _cache_set(key, resp, PRODUCT_TTL)
    return resp.get("data")


async def product_search(q: str, per_page: int = 8, tag: str = "", *,
                         min_price: int = 0, max_price: int = 0,
                         in_stock: bool = False) -> list[dict]:
    params: dict = {"per_page": per_page}
    if q:
        params["q"] = q
    if tag:
        params["tag"] = tag
    if min_price:
        params["min_price"] = min_price
    if max_price:
        params["max_price"] = max_price
    if in_stock:
        params["in_stock"] = 1
    # same TTL as a single product: a search is just many products at once, and
    # `invalidate("product:")` from the Hub webhook clears both
    key = "product:search:" + "&".join(f"{k}={params[k]}" for k in sorted(params))
    cached = _cache_get(key)
    if cached is not None:
        return cached.get("data") or []
    resp = await _get("/products/search", params)
    _cache_set(key, resp, PRODUCT_TTL)
    return resp.get("data") or []


# --- orders (no caching — always live) -------------------------------------

async def orders_lookup(*, phone: str | None = None, email: str | None = None,
                        order_id: int | None = None, order_key: str | None = None,
                        limit: int = 10) -> list[dict]:
    params = {k: v for k, v in {
        "phone": phone, "email": email, "order_id": order_id,
        "order_key": order_key, "limit": limit,
    }.items() if v}
    resp = await _get("/orders/lookup", params)
    return resp.get("data") or []


async def order_tracking(order_id: int) -> dict | None:
    resp = await _get(f"/orders/{order_id}/tracking")
    return resp.get("data")


# --- store content ---------------------------------------------------------

async def store_pages() -> list[dict]:
    key = "pages:index"
    cached = _cache_get(key)
    if cached is not None:
        return cached.get("data") or []
    resp = await _get("/store/pages")
    _cache_set(key, resp, PAGE_TTL)
    return resp.get("data") or []


async def store_page(slug: str) -> dict | None:
    key = f"pages:{slug}"
    cached = _cache_get(key)
    if cached is not None:
        return cached.get("data")
    resp = await _get(f"/store/pages/{slug}")
    _cache_set(key, resp, PAGE_TTL)
    return resp.get("data")


async def health() -> dict:
    resp = await _get("/health")
    return resp.get("data") or {}
