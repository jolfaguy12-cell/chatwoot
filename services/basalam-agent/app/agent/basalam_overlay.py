"""پوشاندن دادهٔ هاب با قیمت و موجودی باسلام.

توضیحات محصول از هاب می‌آید (در باسلام طول توضیحات محدود است و متن کامل آنجا
نیست)، ولی قیمت، موجودی و شناسه‌ای که به مشتری باسلام گفته می‌شود باید از خود
باسلام باشد. این ماژول دادهٔ هاب را سرِ جا اصلاح می‌کند تا بقیهٔ کد بدون تغییر
همان قالب‌بندی قبلی را بدهد.
"""

BASALAM_PRODUCT_URL = "https://basalam.com/p/{id}"


def overlay(product: dict, basalam: dict | None) -> dict | None:
    """محصول هاب + دادهٔ باسلام. اگر در غرفه نباشد None — یعنی برای مشتری
    باسلام اصلاً وجود ندارد و نباید معرفی شود."""
    if not basalam:
        return None

    merged = dict(product)
    price = basalam.get("price_toman")
    primary = basalam.get("primary_price_toman")

    merged["price"] = price
    if primary and price and primary != price:
        merged["regular_price"] = primary
        merged["sale_price"] = price
    else:
        merged["regular_price"] = price
        merged["sale_price"] = None

    merged["stock_status"] = "instock" if basalam.get("available") else "outofstock"
    merged["stock_quantity"] = basalam.get("inventory")

    # مدل‌های سایت قیمت و موجودی سایت را دارند؛ آنچه برای مشتری باسلام معتبر است
    # موجودی همان مدل در همین آگهی است (شمارهٔ ۰۱ صفر باشد و ۰۴ موجود)
    merged["variations"] = [
        {"id": v.get("id"),
         "attributes": v.get("attributes") or {},
         "price": v.get("price_toman"),
         "stock_status": "instock" if (v.get("stock") or 0) > 0 else "outofstock"}
        for v in basalam.get("variants") or []
    ]

    merged["basalam_id"] = basalam.get("id")
    merged["basalam_title"] = basalam.get("title")
    # امتیاز و تعداد نظر فقط سمت باسلام هست و همان چیزی است که مشتری روی همین
    # آگهی می‌بیند؛ مثل قیمت و موجودی، لایه‌ای روی دادهٔ هاب است نه جایگزین آن
    merged["rating"] = basalam.get("rating")
    merged["review_count"] = basalam.get("review_count")
    merged["permalink"] = BASALAM_PRODUCT_URL.format(id=basalam.get("id"))
    return merged


def basalam_only(basalam: dict) -> dict:
    """محصولی که در هاب پیدا نشد.

    توضیحات هاب کامل‌تر است، ولی نبودنش نباید به «پیدا نکردم» و ارجاع به اپراتور
    ختم شود — قیمت، موجودی و مدل‌ها از باسلام هست و برای جواب دادن کافی است.
    """
    return overlay({
        "id": basalam.get("id"),
        "name": basalam.get("title"),
        "short_description": basalam.get("summary") or "",
        "description": basalam.get("description") or "",
    }, basalam)


def overlay_many(products: list[dict], matches: dict[str, dict]) -> list[dict]:
    """فقط محصولاتی برمی‌گردند که در غرفهٔ باسلام هم هستند."""
    out = []
    for product in products:
        merged = overlay(product, matches.get(product.get("name") or ""))
        if merged:
            out.append(merged)
    return out
