"""سفارش‌های غرفه در باسلام (مرسوله‌ها).

توکن باسلام فقط اینجاست، پس خلاصهٔ سفارش هم اینجا ساخته می‌شود. عمداً هیچ
اطلاعات تماس یا آدرس گیرنده برنمی‌گردد: مصرف‌کنندهٔ این داده یک مدل زبانی است و
چیزی که برنگردد نمی‌تواند لو برود. تنها استثنا شمارهٔ پیک موتوری است که خودِ
مشتری باید داشته باشد.
"""
import logging

from . import basalam

log = logging.getLogger("bslm-bridge.orders")

COURIER_METHOD_ID = 3259          # پیک موتوری
DELIVERED_ITEM_STATUS = "تحویل شده"
SATISFIED_PARCEL_STATUS = "رضایت مشتری"


def _toman(rial) -> int | None:
    try:
        return int(rial) // 10
    except (TypeError, ValueError):
        return None


def _brief(parcel: dict) -> dict:
    """مرسوله → همان چیزی که ایجنت اجازه دارد ببیند."""
    method = (parcel.get("shipping_method") or {}).get("current") or {}
    receipt = parcel.get("post_receipt") or {}
    status = (parcel.get("status") or {}).get("title") or ""
    is_courier = method.get("id") == COURIER_METHOD_ID

    items, item_statuses = [], []
    for item in parcel.get("items") or []:
        items.append({
            "id": item.get("id"),
            "title": item.get("title"),
            "quantity": item.get("quantity"),
            "price_toman": _toman(item.get("price")),
        })
        title = ((item.get("last_item_status") or {}).get("status") or {}).get("title")
        if title:
            item_statuses.append(title)

    delivered = bool(parcel.get("is_delivered")) or status == SATISFIED_PARCEL_STATUS or (
        bool(item_statuses) and all(s == DELIVERED_ITEM_STATUS for s in item_statuses))

    return {
        "parcel_id": parcel.get("id"),
        "order_id": (parcel.get("order") or {}).get("id"),
        "created_at": parcel.get("created_at"),
        "paid_at": (parcel.get("order") or {}).get("paid_at"),
        "status": status,
        "delivered": delivered,
        "delivered_at": parcel.get("delivery_at"),
        "sent_at": parcel.get("send_at"),
        "estimate_send_at": parcel.get("estimate_send_at"),
        "has_delay": bool(parcel.get("has_delay")),
        "delay_days": parcel.get("delay_days") or 0,
        "shipping_method": method.get("title") or "",
        "is_courier": is_courier,
        # شمارهٔ پیک تنها شماره‌ای است که به مشتری داده می‌شود؛ روی پست این فیلد
        # یا شمارهٔ خود مشتری است یا شمارهٔ ما، پس هرگز بیرون نمی‌رود.
        "courier_phone": receipt.get("phone_number") if is_courier else None,
        "tracking_code": receipt.get("tracking_code"),
        "tracking_link": receipt.get("tracking_link"),
        "items": items,
        "item_statuses": item_statuses,
        "total_toman": _toman(parcel.get("total_items_price")),
    }


def for_customer(customer_id: int, limit: int = 5) -> list[dict]:
    """مرسوله‌های یک مشتری، تازه‌ترین اول. مرسولهٔ اول با جزئیات کامل خوانده
    می‌شود چون وضعیت تحویل و وضعیت تک‌تک اقلام فقط آنجاست."""
    resp = basalam._client.get("/v1/vendor-parcels",
                               params={"items.customer_ids": str(customer_id), "per_page": 30})
    if resp.status_code >= 400:
        log.warning("parcels for %s failed: %s %s", customer_id, resp.status_code, resp.text[:200])
        raise RuntimeError(f"basalam parcels {resp.status_code}")

    parcels = resp.json().get("data") or []
    parcels.sort(key=lambda p: p.get("created_at") or "", reverse=True)
    parcels = parcels[:limit]
    if parcels:
        detail = one(parcels[0]["id"])
        if detail:
            parcels[0] = detail
    return [_brief(p) for p in parcels]


def one(parcel_id: int) -> dict | None:
    resp = basalam._client.get(f"/v1/vendor-parcels/{parcel_id}")
    if resp.status_code >= 400:
        log.warning("parcel %s failed: %s", parcel_id, resp.status_code)
        return None
    return resp.json()
