"""Agent tools. All Hub access goes through these; the order tool exposes only
orders that were pre-authorized by deterministic Python (never LLM judgment).
Tool outputs are wrapped in <data> blocks (see injection.py)."""

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone

from langchain_core.tools import StructuredTool

from app.agent.injection import strip_html, wrap_data
from app.db import db_session
from app.models import ContentGap
from app.agent import basalam_overlay
from app.services import basalam_client, hub_client, iran_calendar, persian
from app.services.hub_client import HubUnavailable

log = logging.getLogger(__name__)

# The product blocks dominate prompt cost. Expiry/shelf-life is extracted from
# the FULL description separately, so trimming here loses no answerable fact.
DESCRIPTION_LIMIT = 1400
# Search hits carry a snippet so the agent can judge relevance without pulling
# every candidate in full — cheaper than one get_product_details per product.
SEARCH_SUMMARY_CHARS = 150
MAX_SEARCH_RESULTS = 8       # سقف فهرست بعد از افزودن نتیجهٔ جستجوی کوتاه‌شده
VARIANT_DETAIL_RESULTS = 3   # چند نتیجهٔ اول، موجودیِ مدل‌هایشان هم خوانده شود

# Expiry/shelf-life facts hide in prose and attributes; surface them explicitly
# so they are never lost to description truncation. (Persian stores write e.g.
# «📅 تاریخ انقضا: ۲۰۲۹/۰۹» inside post_content.)
LONGEVITY_KEYWORDS = ("انقضا", "ماندگاری", "تاریخ مصرف", "شلف لایف", "تاریخ تولید", "exp")


def extract_longevity_info(product: dict) -> str:
    """Collect every registered expiry/shelf-life mention from the product's
    attributes, short description and FULL description (untruncated)."""
    lines: list[str] = []
    for attr in product.get("attributes") or []:
        blob = persian.normalize(
            f"{attr.get('name', '')} {' '.join(attr.get('options') or [])}"
        ).lower()
        if any(k in blob for k in LONGEVITY_KEYWORDS):
            lines.append(f"{attr.get('name')}: {' | '.join(attr.get('options') or [])}")
    text = strip_html(
        f"{product.get('short_description') or ''}\n{product.get('description') or ''}"
    )
    for raw_line in re.split(r"[\n•✔️✅|]+", text):
        line = persian.normalize(raw_line)
        if line and any(k in line.lower() for k in LONGEVITY_KEYWORDS):
            lines.append(line[:200])
    deduped: list[str] = []
    for line in lines:
        if line not in deduped:
            deduped.append(line)
    return "\n".join(deduped[:8])


def is_available(product: dict) -> bool:
    """A product counts as available when the product itself is in stock OR any
    of its variants is — one sold-out size must not hide the whole product."""
    if (product.get("stock_status") or "") == "instock":
        return True
    return any((v.get("stock_status") or "") == "instock"
               for v in product.get("variations") or [])


def availability_summary(product: dict, *, quantity: bool = True) -> str:
    """`quantity` off for customer-facing text — stock counts are internal."""
    variations = product.get("variations") or []
    in_stock = [v for v in variations if (v.get("stock_status") or "") == "instock"]
    if not is_available(product):
        return "ناموجود"
    if variations and in_stock and len(in_stock) < len(variations):
        return f"موجود ({len(in_stock)} مدل از {len(variations)} مدل موجود است)"
    qty = product.get("stock_quantity")
    return "موجود" + (f" (تعداد: {int(qty)})" if quantity and qty else "")


@dataclass
class ToolContext:
    conversation_id: int
    intent: str = ""
    # پیام خود مشتری: ابزار باید بداند سؤال دربارهٔ کدام مدل است، وگرنه فقط
    # موجودیِ کل آگهی را برمی‌گرداند و «رایحهٔ شکلات موجوده؟» جواب غلط می‌گیرد
    text: str = ""
    product_slug: str = ""
    page_url: str = ""
    cart: dict = field(default_factory=dict)
    shown_products: list[int] = field(default_factory=list)
    handoff_requested: str = ""
    gaps_recorded: list[int] = field(default_factory=list)
    calls: list[dict] = field(default_factory=list)
    data_outputs: list[str] = field(default_factory=list)
    cards: list[dict] = field(default_factory=list)
    # آخرین سفارشی که در همین نوبت خوانده شد — کارت نظر و گزارش تحویل به آن نیاز دارند
    latest_order: dict | None = None
    # اعلان‌هایی که باید به تلگرام ادمین‌ها برود بدون اینکه گفتگو از ایجنت گرفته شود
    alerts: list[dict] = field(default_factory=list)


# Dispatch timing is a policy question; `product` is allowed too because «این رو
# بخرم کی می‌رسه؟» inside a product chat is routed there.
DELIVERY_INTENTS = ("policy", "product")

# handoff reason for «کی شارژ می‌شود؟» — kept in sync with services.handoff.RESTOCK
RESTOCK = "restock"


def _trim_title(title: str, words: int) -> str:
    """چند کلمهٔ اول عنوان، بدون پرانتز و دنبالهٔ لاتین — هاب با عنوان بلند چیزی برنمی‌گرداند."""
    head = re.split(r"[(\-–—|]", (title or "").replace("‌", " "))[0]
    return " ".join(re.sub(r"[A-Za-z0-9]+", " ", head).split()[:words])

# Said to the model when the Basalam catalogue could not be reached. A silent
# empty result would read as «we don't sell that» — which is how a customer
# asking for کرم پودر was told we had none while three were in stock.
CATALOG_DOWN = (
    "خطا: فهرست غرفهٔ باسلام همین حالا در دسترس نیست، پس نتوانستم موجودی و قیمت را"
    " تأیید کنم. **به هیچ عنوان نگو این محصول را نداریم** — نبودِ داده یعنی سیستم"
    " جواب نداد، نه اینکه محصول در غرفه نیست. مؤدبانه بگو یک لحظه مشکل فنی پیش آمده"
    " و request_human_handoff را صدا بزن تا همکار انسانی بررسی کند."
)


REVIEW_URL = "https://basalam.com/account/unrevieweds"

DELIVERY_PROBLEM_REPLY = (
    "به ادمین‌ها گزارش شد. حالا خودت جواب مشتری را بده: راهنمایی تماس با ۱۹۳ در"
    " ساعات اداری برای ثبت شکایت، این نکته که گاهی پستچی زودتر از تحویل واقعی"
    " وضعیت را تحویل‌شده ثبت می‌کند و معمولاً مشکل خاصی نیست، و اطمینان اینکه"
    " موضوع از سمت ما هم پیگیری می‌شود. نگو گفتگو را به کسی منتقل کردی."
)

NO_ORDERS = (
    "این مشتری هیچ سفارشی در غرفهٔ ما ندارد. پس سؤالش پیگیری سفارش نیست —"
    " احتمالاً دربارهٔ شیوهٔ ارسال، هزینهٔ ارسال یا زمان آماده‌سازی است."
    " نگو «سفارشی پیدا نکردم» و بی‌خودی شمارهٔ سفارش نخواه؛ خودت بپرس چه کمکی"
    " می‌توانی بکنی و اگر سؤالش دربارهٔ ارسال بود با ابزارهای مربوطه جواب بده."
)

ORDERS_DOWN = (
    "خطا: دسترسی به سفارش‌های باسلام همین حالا ممکن نشد. **نگو سفارشی نداری** —"
    " این یعنی سیستم جواب نداد. کوتاه بگو مشکل فنی کوتاهی پیش آمده و"
    " request_human_handoff را صدا بزن."
)


def fa_date(iso: str | None) -> str:
    """۲۰۲۶-۰۷-۲۹T… → «۷ مرداد». مشتری تاریخ میلادی را نمی‌فهمد."""
    if not iso:
        return ""
    try:
        import jdatetime

        day = datetime.fromisoformat(iso.replace("Z", "")).date()
        jalali = jdatetime.date.fromgregorian(date=day)
        return f"{jalali.day} {jalali.j_months_fa[jalali.month - 1]}"
    except Exception:  # noqa: BLE001 — تاریخ نمایشی ارزش خطا دادن ندارد
        return ""


# What the agent may say for each parcel status — deterministic, so the model
# never improvises a delivery promise.
# همان عددی که برای مرسولهٔ ارسال‌شده هم می‌گوییم — یک منبع، دو جا
TRANSIT_DAYS = ("بعد از ارسال معمولاً ۲ تا ۴ روز طول می‌کشد تا به دست مشتری برسد؛"
                " زمان دقیقش دست پست است و ما آن را نمی‌دانیم.")

PARCEL_GUIDANCE = {
    "در حال آماده‌سازی": ("سفارش در حال آماده‌سازی است: بگو ظرف یک روز بسته‌بندی و"
                          " تحویل پیشخوان پست می‌شود. کد رهگیری هنوز وجود ندارد و"
                          " قولش را هم نده."),
    "ارسال شده": ("سفارش ارسال شده: کد رهگیری و لینک رهگیری را بده و بگو پیش‌بینی"
                  " می‌شود طی ۲ تا ۴ روز آینده به دستشان برسد."),
}
COURIER_GUIDANCE = ("این سفارش با پیک موتوری رفته: بگو بسته ارسال شده، شمارهٔ پیک را"
                    " بده و از مشتری بخواه بعد از تحویل گرفتن بسته خبر بدهد.")
DELIVERED_GUIDANCE = ("باسلام این سفارش را تحویل‌شده ثبت کرده. اگر مشتری می‌گوید به دستش"
                      " نرسیده، report_delivery_problem را صدا بزن. در غیر این صورت"
                      " **حتماً** send_review_request را صدا بزن و بعد در متن، مهربان"
                      " خواهش کن نظرشان را ثبت کنند — این را به خودشان واگذار نکن و"
                      " «اگر رسیده…» هم نگو.")


def format_parcel(order: dict) -> str:
    lines = [
        f"شماره سفارش: {order.get('order_id')}",
        f"تاریخ ثبت: {fa_date(order.get('paid_at') or order.get('created_at'))}",
        f"وضعیت در باسلام: {order.get('status')}",
        f"روش ارسال: {order.get('shipping_method')}",
    ]
    if order.get("tracking_code"):
        lines.append(f"کد رهگیری: {order['tracking_code']}")
    if order.get("tracking_link"):
        lines.append(f"لینک رهگیری: {order['tracking_link']}")
    if order.get("courier_phone"):
        lines.append(f"شمارهٔ پیک: {order['courier_phone']}")
    if order.get("sent_at"):
        lines.append(f"تاریخ ارسال: {fa_date(order['sent_at'])}")
    items = order.get("items") or []
    if items:
        lines.append("اقلام:")
        for item in items:
            qty = f" × {item['quantity']}" if (item.get("quantity") or 1) > 1 else ""
            lines.append(f"  - {item.get('title')}{qty}")
    if order.get("total_toman"):
        lines.append(f"مبلغ کل: {persian.fa_amount(order['total_toman'])} تومان")

    guidance = []
    if order.get("delivered"):
        guidance.append(DELIVERED_GUIDANCE)
    else:
        hint = PARCEL_GUIDANCE.get(order.get("status") or "")
        if hint:
            guidance.append(hint)
        if order.get("is_courier"):
            guidance.append(COURIER_GUIDANCE)
    if guidance:
        lines.append("راهنمای پاسخ: " + " ".join(guidance))
    return "\n".join(lines)


def _record(ctx: ToolContext, name: str, args: dict, ok: bool, note: str = "") -> None:
    ctx.calls.append({"tool": name, "args": args, "ok": ok, "note": note[:200]})


# «اصل» / «اصلی» به‌عنوان یک واژهٔ کامل. بدون مرز، «فاصله» و «اصلاح» هم اصالت
# حساب می‌شدند و ایجنت اصل بودنِ کالایی را تأیید می‌کرد که چنین ادعایی ندارد.
GENUINE_RE = re.compile(r"(?:^|[\s‌(«\-])اصل(?:ی)?(?:$|[\s‌)»\-،.])")


def is_genuine(product: dict) -> bool:
    """آیا فروشنده روی خودِ عنوان آگهی «اصل» زده است."""
    return any(GENUINE_RE.search(f" {t} ") for t in
               (product.get("name") or "", product.get("basalam_title") or "") if t)


def format_product(product: dict, *, full: bool = True) -> str:
    # The stock *count* is deliberately hidden: in Basalam a listing like
    # «(تعداد: ۴)» got read back to the customer as «فقط شمارهٔ ۴ موجود است».
    lines = [
        f"نام محصول: {product.get('name')}",
        f"شناسه: {product.get('id')} | پیوند: {product.get('permalink')}",
        f"وضعیت موجودی: {availability_summary(product, quantity=False)}",
    ]
    if product.get("basalam_id"):
        lines.append("توجه: موجودی هر مدل جداست و در فهرست «مدل‌ها» آمده. اگر مشتری دربارهٔ"
                     " یک شماره یا رنگ خاص پرسید، فقط از همان سطر جواب بده؛ از «موجود بودن"
                     " محصول» یا از فهرست رنگ‌بندیِ توضیحات نتیجه نگیر که همهٔ مدل‌ها هستند.")
    price = product.get("price")
    regular = product.get("regular_price")
    sale = product.get("sale_price")
    if sale and regular:
        lines.append(f"قیمت اصلی: {regular:,.0f} تومان | قیمت با تخفیف: {sale:,.0f} تومان")
    elif price is not None:
        lines.append(f"قیمت: {price:,.0f} تومان")
    else:
        lines.append("قیمت: ثبت نشده")
    if is_genuine(product):
        lines.append("اصالت: روی عنوان همین آگهی «اصل» ثبت شده. اگر مشتری پرسید اصل است یا"
                     " نه، با اطمینان بگو بله اصل است.")
    reviews = product.get("review_count") or 0
    if reviews:
        rating = product.get("rating")
        lines.append(f"نظر خریداران روی همین آگهی: {persian.fa_amount(reviews)} نظر ثبت‌شده"
                     + (f" با امتیاز {persian.fa_digits(str(rating))} از ۵" if rating else "")
                     + ". برای اطمینان‌دادن به مشتری به همین اشاره کن و بگو می‌تواند"
                       " نظرها را پایین همین صفحهٔ محصول ببیند.")
    for t in ("brands", "categories"):
        vals = [x["name"] for x in product.get(t) or []]
        if vals:
            lines.append(("برند: " if t == "brands" else "دسته‌بندی: ") + "، ".join(vals))
    for attr in product.get("attributes") or []:
        if attr.get("options"):
            lines.append(f"ویژگی {attr['name']}: " + " | ".join(attr["options"]))
    variations = product.get("variations") or []
    if variations:
        lines.append(f"مدل‌ها ({len(variations)}):")
        for v in variations:
            attrs = "، ".join(f"{k}: {val}" for k, val in (v.get("attributes") or {}).items())
            vprice = v.get("sale_price") or v.get("price")
            price_txt = f"{vprice:,.0f} تومان" if vprice else "قیمت ثبت نشده"
            # همان نشانهٔ سطرهای جستجو: «outofstock» انگلیسی را نه مدل جدی می‌گرفت
            # نه اعتبارسنج، و پاسخِ درستِ «شمارهٔ ۱ ناموجود است» رد می‌شد
            stock = "[موجود]" if (v.get("stock_status") or "") == "instock" else "[ناموجود]"
            lines.append(f"  - {stock} {attrs or 'مدل ' + str(v.get('id'))} | {price_txt}")
    if full:
        short_desc = strip_html(product.get("short_description"))
        desc = strip_html(product.get("description"))
        if short_desc:
            lines.append("خلاصه ثبت‌شده محصول:")
            lines.append(short_desc[:DESCRIPTION_LIMIT // 2])
        if desc:
            lines.append("توضیحات ثبت‌شده محصول:")
            lines.append(desc[:DESCRIPTION_LIMIT])
        longevity = extract_longevity_info(product)
        lines.append("اطلاعات ماندگاری/انقضای ثبت‌شده در این محصول:")
        lines.append(longevity if longevity else "(هیچ موردی ثبت نشده است)")
    return "\n".join(lines)


GIFT_TAG = "مناسب هدیه"    # the shop's own gift tag (/product-tag/مناسب-هدیه/)
GIFT_PICKS = 3
GIFT_POOL = 10             # how many candidates we pull details for before picking

MAX_CARDS = 4                # distinct products per answer
MAX_VARIATION_BUTTONS = 6    # models of one product (شماره/رنگ/حجم) on its card
MAX_TOTAL_CARDS = 4

# «تکی» و «بسته/عمده» دو آگهی جدای غرفه‌اند، نه دو مدل از یک آگهی. مشتری کارت
# آگهی تکی را می‌فرستد و می‌پرسد «پک موجوده؟»، و مدل چون کارت جلوی چشمش است
# سراغ جستجوی تازه نمی‌رود و از رنگبندی همان آگهی جواب می‌دهد — یعنی «فقط تکی
# داریم»، در حالی که پک موجود است. پس آگهی‌های هم‌خانواده همیشه همراه جزئیات
# محصول می‌آیند و به تصمیم مدل واگذار نمی‌شوند.
SIBLING_QUERY_WORDS = 3      # «خط لب دراگون» — عبارتی که فهرست نامزدها را می‌آورد
SIBLING_ANCHOR_WORDS = 2     # «خط لب» — دستهٔ کالا باید یکی باشد
SIBLING_OVERLAP = 0.5        # و بیش از نیمِ واژه‌های عنوان کوتاه‌تر مشترک باشد
MAX_SIBLINGS = 3


def _product_image(product: dict) -> str:
    images = product.get("images") or []
    thumb = next((i for i in images if i.get("is_thumbnail")), None) or (images[0] if images else {})
    return thumb.get("url") or ""


def _price_line(price, regular) -> str:
    text = f"{persian.fa_amount(price)} تومان" if price else "قیمت ثبت نشده"
    if price and regular and regular > price:
        text += f" (به‌جای {persian.fa_amount(regular)} تومان)"
    return text


def _variation_label(variation: dict) -> str:
    """«شماره ۳» for a numeric model, but «بسته کامل» on its own — prefixing a
    descriptive value with the attribute name just reads wrong."""
    parts = []
    for name, value in (variation.get("attributes") or {}).items():
        value = persian.fa_digits(str(value)).strip()
        parts.append(value if " " in value else f"{name} {value}")
    return "، ".join(parts)


def _card_ids(product: dict, requested_id: int) -> list[int]:
    """هر شناسه‌ای که این محصول را صدا می‌زند: شناسهٔ هاب، شناسهٔ باسلام و همانی که
    مدل داد. dedup روی هر سه کار می‌کند، وگرنه محصولی که با شناسهٔ باسلام کارت شده
    دفعهٔ بعد با شناسهٔ هاب دوباره کارت می‌گیرد."""
    return [i for i in {int(requested_id), product.get("id"), product.get("basalam_id")}
            if isinstance(i, int)]


def _card_evidence(product: dict, product_id: int) -> str:
    return (f"- id={product_id} | [{availability_summary(product)}] {product.get('name', '')}"
            f" | {_price_line(product.get('price'), product.get('regular_price'))}")


def build_product_card(product: dict) -> dict:
    """Chatwoot `cards` item for a simple product. The add-to-cart button is a
    postback the WordPress plugin turns into a real WooCommerce cart add."""
    desc = _price_line(product.get("price"), product.get("regular_price"))
    desc += f" — {availability_summary(product, quantity=False)}"
    actions: list[dict] = []
    if is_available(product):
        actions.append({"type": "postback", "text": "افزودن به سبد",
                        "payload": f"add_to_cart:{product['id']}:1"})
    actions.append({"type": "link", "text": "جزئیات محصول",
                    "uri": product.get("permalink", "")})
    return {"title": (product.get("name") or "")[:120], "description": desc,
            "media_url": _product_image(product), "actions": actions}


def build_variable_product_card(product: dict) -> dict:
    """One card for a product with models — a button per in-stock model, so the
    customer taps «شماره ۳» and it goes straight into the cart. WooCommerce
    accepts the variation id directly, which is what the postback carries."""
    in_stock = [v for v in (product.get("variations") or [])
                if (v.get("stock_status") or "") == "instock"][:MAX_VARIATION_BUTTONS]
    prices = [v.get("sale_price") or v.get("price") for v in in_stock]
    prices = [p for p in prices if p]
    if prices and min(prices) != max(prices):
        desc = f"از {persian.fa_amount(min(prices))} تا {persian.fa_amount(max(prices))} تومان"
    else:
        desc = _price_line(prices[0] if prices else product.get("price"),
                           product.get("regular_price"))
    desc += f" — {persian.fa_amount(len(in_stock))} مدل موجود"
    actions = [{"type": "postback",
                "text": _variation_label(v) or "افزودن به سبد",
                "payload": f"add_to_cart:{v['id']}:1"} for v in in_stock]
    actions.append({"type": "link", "text": "جزئیات محصول",
                    "uri": product.get("permalink", "")})
    return {"_id": product["id"], "title": (product.get("name") or "")[:120],
            "description": desc, "media_url": _product_image(product), "actions": actions}


async def _pick_by_category(candidates: list[dict], count: int, matches: dict) -> list[dict]:
    """Closest-to-budget first, but at most one product per category so the
    customer gets real alternatives instead of three of the same thing.

    The detail fetch returns the plain Hub product, so the Basalam price, stock
    and id have to be laid over it again.
    """
    ranked = sorted(candidates, key=lambda p: p.get("price") or 0, reverse=True)[:GIFT_POOL]
    detailed = []
    for row in ranked:
        try:
            product = await hub_client.product_by_id(row["id"])
        except HubUnavailable:
            continue
        merged = basalam_overlay.overlay(product, matches.get((product or {}).get("name") or ""))
        if merged:
            detailed.append(merged)
    picks: list[dict] = []
    used_categories: set[str] = set()
    for product in detailed:
        category = (product.get("categories") or [{}])[0].get("name", "")
        if category and category in used_categories:
            continue
        used_categories.add(category)
        picks.append(product)
        if len(picks) == count:
            return picks
    for product in detailed:  # not enough distinct categories — fill up
        if product not in picks:
            picks.append(product)
        if len(picks) == count:
            break
    return picks


def _variant_name(variant: dict | None) -> str:
    return "، ".join(str(v) for v in ((variant or {}).get("attributes") or {}).values())


def _significant_words(text: str) -> set[str]:
    return {w for w in persian.normalize(text).split() if len(w) >= 3}


def _requested_variant(query: str, variants: list[dict]) -> dict | None:
    """مدلی (رایحه/رنگ/شماره) که مشتری اسمش را برده.

    «رایحهٔ شکلات کی شارژ می‌شود؟» سؤال دربارهٔ یک مدل است، ولی موجودیِ آگهی
    مدل‌ها را با هم قاطی می‌کند. بدون این تطبیق جواب «موجود است» می‌شود — همان
    چیزی که به مشتری گفتیم در حالی که آن رایحه صفر بود.

    تطبیق سرِ واژه است نه دقیق، چون مشتری «شکلات» می‌گوید و مدل «کیک شکلات» است
    و «دونات» در برابر «دوناتی».
    """
    asked = _significant_words(query)
    for variant in variants:
        values = _significant_words(_variant_name(variant))
        if any(v.startswith(a) or a.startswith(v) for v in values for a in asked):
            return variant
    return None


def asked_variant_note(query: str, product: dict) -> str:
    """اگر سؤال دربارهٔ مدلی است که موجود نیست، همان را صریح به مدل بگو.

    بدون این، جواب به قضاوت مدل و اعتبارسنج سپرده می‌شد: آگهی [موجود] است و مدلِ
    پرسیده‌شده [ناموجود]، و نتیجه گاهی «بله موجود است» می‌شد — همان چیزی که به
    مشتری گفتیم و نبود.
    """
    variation = _requested_variant(query, product.get("variations") or [])
    if not variation or (variation.get("stock_status") or "") == "instock":
        return ""
    in_stock = [_variation_label(v) for v in product.get("variations") or []
                if (v.get("stock_status") or "") == "instock"]
    return (f"\nتوجه: مشتری دربارهٔ «{_variation_label(variation)}» پرسیده و این مدل"
            " [ناموجود] است، هرچند خود آگهی موجود است. در پاسخ **صریح** بگو همین"
            " مدل الان موجود نیست"
            + (f" و مدل‌های موجود را نام ببر: {'، '.join(in_stock)}." if in_stock else ".")
            + " نگو این مدل موجود است.")


def _title_tokens(name: str) -> set[str]:
    return set(re.sub(r"[A-Za-z0-9(\-–—|]+", " ", (name or "").replace("‌", " ")).split())


def _is_sibling(name: str, tokens: set[str], anchor: str) -> bool:
    """همان کالا با آگهی دیگر، نه هر همسایه‌ای در همان دسته.

    پیشوند مشترک به‌تنهایی کافی نیست: «پک کرم دست و بالم لب کرومی» و «پک کرم دست
    ۶ عددی سادور» هر دو با «پک کرم دست» شروع می‌شوند ولی یک کالا نیستند. پس
    دستهٔ کالا (دو واژهٔ اول) باید یکی باشد **و** بیشتر واژه‌های عنوان مشترک.
    شمردن واژه‌ها روی عنوان کوتاه‌تر است تا «تکی» و «بستهٔ ۱۲ عددی» — که یکی‌شان
    همیشه چند واژه بلندتر است — از هم جدا نیفتند.
    """
    other = _title_tokens(name)
    if not other or _trim_title(name, SIBLING_ANCHOR_WORDS) != anchor:
        return False
    return len(tokens & other) / min(len(tokens), len(other)) >= SIBLING_OVERLAP


async def _sibling_listings(product: dict) -> list[dict]:
    """آگهی‌های دیگر همین کالا در غرفه (تکی در برابر بسته و عمده).

    نتیجه از فیلتر باسلام هم رد می‌شود، پس آگهی‌ای که در غرفه نیست اصلاً به مدل
    نشان داده نمی‌شود.
    """
    name = product.get("name") or ""
    anchor = _trim_title(name, SIBLING_ANCHOR_WORDS)
    tokens = _title_tokens(name)
    if len(anchor.split()) < SIBLING_ANCHOR_WORDS or not tokens:
        return []
    try:
        rows = await hub_client.product_search(_trim_title(name, SIBLING_QUERY_WORDS),
                                               per_page=10)
        family = [r for r in rows if _is_sibling(r.get("name") or "", tokens, anchor)]
        if not family:
            return []
        matches = await basalam_client.match_titles([r.get("name", "") for r in family])
    except (HubUnavailable, basalam_client.CatalogUnavailable):
        # این فهرست افزوده است؛ نبودنش نباید جواب اصلی محصول را از بین ببرد
        return []
    seen = {product.get("id"), product.get("basalam_id")}
    return [p for p in basalam_overlay.overlay_many(family, matches)
            if p.get("id") not in seen and p.get("basalam_id") not in seen][:MAX_SIBLINGS]


def build_tools(ctx: ToolContext) -> list[StructuredTool]:
    async def get_current_page_product() -> str:
        slug = ctx.product_slug or persian.slug_from_url(ctx.page_url)
        if not slug:
            _record(ctx, "get_current_page_product", {}, False, "no page context")
            return "مشتری روی صفحه محصول مشخصی نیست. از او بپرس کدام محصول را می‌گوید."
        try:
            product = await hub_client.product_by_slug(slug)
        except HubUnavailable as e:
            _record(ctx, "get_current_page_product", {"slug": slug}, False, str(e))
            return "خطا: دسترسی به اطلاعات فروشگاه فعلاً ممکن نیست."
        if not product:
            _record(ctx, "get_current_page_product", {"slug": slug}, False, "not found")
            return ("محصولی با این آدرس صفحه پیدا نشد (شاید حذف یا جابه‌جا شده)."
                    " از مشتری نام محصول را بپرس یا با search_products جستجو کن.")
        _record(ctx, "get_current_page_product", {"slug": slug}, True, product.get("name", ""))
        out = wrap_data("current_product", format_product(product))
        ctx.data_outputs.append(out)
        return out

    async def search_products(query: str = "", tag: str = "") -> str:
        widened = ""
        try:
            results = await hub_client.product_search(query, tag=tag)
            short = _trim_title(query, SIBLING_QUERY_WORDS)
            if short and len(query.split()) > SIBLING_QUERY_WORDS:
                # عبارت بلند دو جور کالای موجود را پنهان می‌کند: یا هیچ نمی‌دهد
                # (و فهرست خالی برای مدل یعنی «نداریم»)، یا فقط بخشی از خانواده را
                # می‌آورد — «خط لب دراگون نود پک کامل» تکی‌ها را می‌دهد و بسته را نه.
                # جستجوی کوتاه‌ترِ همان عبارت، خانواده را کامل می‌کند.
                extra = await hub_client.product_search(short, tag=tag)
                seen = {p.get("id") for p in results}
                fresh = [p for p in extra if p.get("id") not in seen]
                if fresh:
                    widened = short
                    results = (results + fresh)[:MAX_SEARCH_RESULTS]
        except HubUnavailable as e:
            _record(ctx, "search_products", {"query": query, "tag": tag}, False, str(e))
            return "خطا: جستجوی محصولات فعلاً در دسترس نیست."
        # هاب فارسی را خوب می‌فهمد، ولی قیمت و موجودی مشتری باسلام باید از
        # باسلام بیاید؛ محصولی که در غرفه نیست اصلاً معرفی نمی‌شود.
        try:
            matches = await basalam_client.match_titles([p.get("name", "") for p in results])
        except basalam_client.CatalogUnavailable as e:
            _record(ctx, "search_products", {"query": query, "tag": tag}, False, str(e))
            return CATALOG_DOWN
        results = basalam_overlay.overlay_many(results, matches)
        _record(ctx, "search_products", {"query": query, "tag": tag, "widened": widened}, True,
                f"{len(results)} results in basalam")
        if not results:
            return "هیچ محصولی با این عبارت در غرفهٔ باسلام پیدا نشد."
        # available first — the customer is shopping, not auditing the catalogue
        results.sort(key=lambda p: not is_available(p))
        # فهرست غرفه مدل‌ها را ندارد، پس «[موجود]» اینجا یعنی خودِ آگهی — و آگهیِ
        # «کره بدن در پنج رایحه» موجود است حتی وقتی رایحهٔ شکلاتش صفر باشد. همین
        # باعث شد به مشتری بگوییم رایحه‌ای که نداریم موجود است. خودِ آگهی مدل‌ها را
        # دارد و پل کشش می‌کند (چند میلی‌ثانیه)، پس نتیجه‌های اول را کامل می‌کنیم.
        for p in results[:VARIANT_DETAIL_RESULTS]:
            detailed = await basalam_client.product(p.get("basalam_id") or p["id"])
            if detailed and detailed.get("variants"):
                p.update(basalam_overlay.overlay(p, detailed) or {})
        lines = []
        for index, p in enumerate(results):
            price = f"{p['price']:,.0f} تومان" if p.get("price") else "قیمت ثبت نشده"
            # availability first and bracketed: readers (model and validator both)
            # missed it when it trailed a long line
            stock = f"[{availability_summary(p, quantity=False)}]"
            line = f"- id={p['id']} | {stock} {p['name']} | {price}"
            if p.get("summary"):
                line += f"\n  توضیح: {p['summary'][:SEARCH_SUMMARY_CHARS]}"
            # مدل‌ها فقط برای نتیجهٔ اول، وگرنه یک جستجو ده‌ها سطر می‌شود
            if index == 0:
                for v in p.get("variations") or []:
                    mark = "[موجود]" if (v.get("stock_status") or "") == "instock" else "[ناموجود]"
                    line += f"\n    {mark} {_variation_label(v)}"
            lines.append(line)
        note = asked_variant_note(ctx.text, results[0])
        if note:
            lines.append(note.strip())
        lines.append("«[موجود]» جلوی هر آگهی یعنی کل آگهی، نه یک مدل. اگر مدل‌های"
                     " نتیجهٔ اول بالا آمده، موجودیِ همان مدل را از همان‌جا بگو؛"
                     " برای مدل‌های آگهی‌های دیگر get_product_details را بزن.")
        if widened:
            lines.append(f"نتیجهٔ جستجوی کوتاه‌ترِ «{widened}» هم به این فهرست اضافه شد،"
                         " چون عبارت کامل همهٔ آگهی‌های همان کالا (مثلاً بستهٔ همان محصول)"
                         " را نمی‌آورد. عنوان‌ها را بخوان و فقط چیزی را که واقعاً در"
                         " فهرست هست معرفی کن.")
        out = wrap_data("product_search", "\n".join(lines))
        ctx.data_outputs.append(out)
        return out

    async def _resolve(product_id: int) -> dict | None:
        """شناسه را به «محصول هاب + قیمت و موجودی باسلام» تبدیل می‌کند.

        مشتری باسلام معمولاً کارت محصول می‌فرستد، پس شناسه‌ای که مدل می‌بیند
        شناسهٔ باسلام است نه هاب. هر دو حالت باید کار کند.
        """
        product = await hub_client.product_by_id(int(product_id))
        if product:
            matches = await basalam_client.match_titles([product.get("name", "")])
            match = matches.get(product.get("name", ""))
            if match:
                # فهرست غرفه مدل‌ها را ندارد؛ خود آگهی دارد
                match = await basalam_client.product(match["id"]) or match
            return basalam_overlay.overlay(product, match)

        # شناسهٔ باسلام: از روی عنوانش محصول هاب را پیدا کن تا توضیحات کامل را داشته باشیم
        basalam_product = await basalam_client.product(int(product_id))
        if not basalam_product or not basalam_product.get("title"):
            return None
        row = await _hub_twin(basalam_product)
        if not row:
            # در هاب نبود؛ با دادهٔ خود باسلام جواب بده، نه «پیدا نکردم»
            return basalam_overlay.basalam_only(basalam_product)
        full = await hub_client.product_by_id(row["id"]) or row
        return basalam_overlay.overlay(full, basalam_product)

    async def _hub_twin(basalam_product: dict) -> dict | None:
        """همان محصول در هاب — با تأیید، نه با اعتماد به نتیجهٔ اول.

        عنوان باسلام و هاب همیشه یکی نیست («بیرنگ» در برابر «بی‌رنگ»)، پس جستجو با
        عنوان کامل گاهی هیچ نمی‌دهد. کوتاه‌ترش می‌کنیم، ولی نتیجه را فقط وقتی قبول
        می‌کنیم که تطبیق‌گر خودمان آن را به **همین** شناسهٔ باسلام برگرداند — وگرنه
        توضیحات یک محصول دیگر را به این می‌چسبانیم.
        """
        title = basalam_product.get("title") or ""
        basalam_id = int(basalam_product["id"])
        tried: set[str] = set()
        for query in (title, _trim_title(title, 4), _trim_title(title, 2)):
            if not query or query in tried:
                continue
            tried.add(query)
            # هرچه عبارت کوتاه‌تر، نتیجه پراکنده‌تر — پس دامنه را بازتر می‌گیریم و
            # درستی را به تأیید تطبیق‌گر می‌سپاریم، نه به رتبهٔ جستجو
            rows = await hub_client.product_search(query, per_page=3 if query == title else 20)
            if not rows:
                continue
            matches = await basalam_client.match_titles([r.get("name", "") for r in rows])
            for row in rows:
                twin = matches.get(row.get("name", ""))
                if twin and int(twin["id"]) == basalam_id:
                    return row
        return None

    async def get_product_details(product_id: int) -> str:
        try:
            product = await _resolve(product_id)
        except HubUnavailable as e:
            _record(ctx, "get_product_details", {"product_id": product_id}, False, str(e))
            return "خطا: دسترسی به اطلاعات محصول فعلاً ممکن نیست."
        except basalam_client.CatalogUnavailable as e:
            _record(ctx, "get_product_details", {"product_id": product_id}, False, str(e))
            return CATALOG_DOWN
        if not product:
            _record(ctx, "get_product_details", {"product_id": product_id}, False, "not found")
            return ("محصولی با این شناسه پیدا نشد. اگر مشتری کارت محصول فرستاده،"
                    " اسم همان محصول را با search_products جستجو کن.")
        _record(ctx, "get_product_details", {"product_id": product_id}, True,
                product.get("name", ""))
        body = format_product(product) + asked_variant_note(ctx.text, product)
        siblings = await _sibling_listings(product)
        if siblings:
            body += ("\n\nآگهی‌های مرتبط در غرفه (تکی، بسته، عمده، حجم دیگر):\n"
                     + "\n".join(_card_evidence(p, p["id"]) for p in siblings)
                     + "\nعنوان‌ها را بخوان. اگر مشتری سراغ پک، بسته، ست، عمده یا حجم"
                       " بزرگ‌تر را گرفت و اینجا آگهی‌اش هست، همان را معرفی کن و کارتش را"
                       " بفرست؛ آن‌وقت «فقط تکی داریم» غلط است. آگهی‌ای را که عنوانش"
                       " کالای دیگری است به‌جای این محصول جا نزن.")
        out = wrap_data("product", body)
        ctx.data_outputs.append(out)
        return out

    async def get_my_order() -> str:
        """آخرین سفارش همین مشتری در باسلام. هویت از خودِ گفتگو می‌آید، پس هیچ
        شماره‌ای از مشتری پرسیده نمی‌شود."""
        if ctx.latest_order:
            # the validation revision loop replays the tool calls; the parcel has
            # not changed in those few seconds
            return wrap_data("customer_orders", format_parcel(ctx.latest_order))
        try:
            found = await basalam_client.orders(ctx.conversation_id)
        except basalam_client.CatalogUnavailable as e:
            _record(ctx, "get_my_order", {}, False, str(e))
            return ORDERS_DOWN
        if not found:
            _record(ctx, "get_my_order", {}, True, "no orders")
            out = wrap_data("customer_orders", NO_ORDERS)
            ctx.data_outputs.append(out)
            return out

        latest = found[0]
        ctx.latest_order = latest
        _record(ctx, "get_my_order", {}, True,
                f"parcel {latest['parcel_id']} — {latest['status']}")
        body = format_parcel(latest)
        if len(found) > 1:
            body += f"\n\nاین مشتری {len(found)} سفارش دارد؛ بالا تازه‌ترین است." \
                    " فقط دربارهٔ همین حرف بزن مگر خودش سفارش دیگری را بپرسد."
        out = wrap_data("customer_orders", body)
        ctx.data_outputs.append(out)
        return out

    async def send_review_request() -> str:
        """کارت «ثبت تجربهٔ خرید» باسلام برای اقلام آخرین سفارش."""
        order = ctx.latest_order
        if not order:
            _record(ctx, "send_review_request", {}, False, "no order loaded")
            return "اول get_my_order را صدا بزن."
        if not order.get("delivered"):
            _record(ctx, "send_review_request", {}, False, "not delivered")
            return "این سفارش هنوز تحویل نشده؛ درخواست ثبت نظر زود است."
        item_ids = [i["id"] for i in order.get("items") or [] if i.get("id")]
        result = await basalam_client.send_review_cards(ctx.conversation_id, item_ids)
        fresh = len(result.get("basalam_message_ids") or [])
        already = result.get("already_sent") or 0
        _record(ctx, "send_review_request", {"items": item_ids}, bool(result),
                f"{fresh} sent, {already} already")
        if already and not fresh:
            return ("کارت ثبت نظر قبلاً برای این سفارش رفته بود، پس دوباره فرستاده نشد."
                    " فقط یک جملهٔ کوتاه بنویس که اگر نظرشان را ثبت کنند ممنون می‌شویم.")
        if not fresh:
            return ("کارت ثبت نظر فرستاده نشد. به‌جایش از مشتری خواهش کن نظرش را از این"
                    f" صفحه ثبت کند: {REVIEW_URL}")
        return ("کارت ثبت تجربهٔ خرید برای اقلام این سفارش فرستاده شد. یک جملهٔ کوتاه و"
                " مهربان بنویس که اگر لطف کنند نظرشان را ثبت کنند خوشحال می‌شویم؛ اسم"
                " اقلام را دوباره نیاور و لینکی هم نده.")

    def report_delivery_problem(details: str) -> str:
        """مشتری می‌گوید نرسیده در حالی که باسلام تحویل خورده — اپراتورها باید
        بدانند، ولی گفتگو دست ایجنت می‌ماند تا راهنمایی ۱۹۳ را خودش بدهد."""
        order = ctx.latest_order or {}
        if any(a["kind"] == "delivery_problem" for a in ctx.alerts):
            # a revision pass calls the tool again; the admins need one notice
            return DELIVERY_PROBLEM_REPLY
        ctx.alerts.append({
            "kind": "delivery_problem",
            "text": details[:400],
            "order_id": order.get("order_id"),
            "parcel_id": order.get("parcel_id"),
            "tracking_code": order.get("tracking_code"),
            "status": order.get("status"),
        })
        _record(ctx, "report_delivery_problem", {}, True, details[:80])
        return DELIVERY_PROBLEM_REPLY

    async def get_store_policy(topic: str) -> str:
        # Admin-maintained facts (shipping costs, ...) come first: the store's
        # WordPress pages don't carry them, so this is the only source of truth.
        from app.agent import prompt_store

        facts = prompt_store.get("store_facts")[0].strip()
        try:
            pages = await hub_client.store_pages()
        except HubUnavailable as e:
            if facts:
                _record(ctx, "get_store_policy", {"topic": topic}, True, "store_facts only")
                out = wrap_data("store_facts", facts)
                ctx.data_outputs.append(out)
                return out
            _record(ctx, "get_store_policy", {"topic": topic}, False, str(e))
            return "خطا: دسترسی به صفحات فروشگاه فعلاً ممکن نیست."
        topic_norm = persian.normalize(topic)
        matches = [p for p in pages
                   if topic_norm and topic_norm in persian.normalize(p.get("title", ""))]
        if not matches:
            titles = "، ".join(p["title"] for p in pages[:15])
            _record(ctx, "get_store_policy", {"topic": topic}, bool(facts), "store_facts only")
            out = wrap_data("store_facts",
                            f"{facts}\n\nصفحات فروشگاه: {titles}" if facts else "")
            if facts:
                ctx.data_outputs.append(out)
                return out
            return f"صفحه‌ای با این موضوع پیدا نشد. صفحات موجود: {titles}"
        page = await hub_client.store_page(matches[0]["slug"])
        _record(ctx, "get_store_policy", {"topic": topic}, True, matches[0]["title"])
        text = (page or {}).get("content_text", "")[:DESCRIPTION_LIMIT]
        body = f"{matches[0]['title']}\n{text}"
        out = wrap_data("store_page", f"{facts}\n\n{body}" if facts else body)
        ctx.data_outputs.append(out)
        return out

    async def suggest_gifts(budget_max: int = 0, budget_min: int = 0) -> str:
        """Gift picker: strictly the shop's own «مناسب هدیه» tag, in stock, filtered
        by budget and by what this customer has already been shown, then one product
        per category so the three suggestions are genuinely different."""
        if not budget_max:
            _record(ctx, "suggest_gifts", {}, False, "no budget")
            return "اول بودجهٔ مشتری را بپرس (مثلاً «تا چه مبلغی مدنظرتونه؟») و بعد دوباره این ابزار را صدا بزن."
        seen = set(ctx.shown_products)

        try:
            rows = await hub_client.product_search(
                "", per_page=24, tag=GIFT_TAG, min_price=budget_min,
                max_price=budget_max, in_stock=True)
        except HubUnavailable as e:
            _record(ctx, "suggest_gifts", {"budget_max": budget_max}, False, str(e))
            return "خطا: فهرست محصولات فعلاً در دسترس نیست."
        # فقط هدیه‌هایی که در غرفهٔ باسلام هم هستند؛ قیمت و موجودی از باسلام
        try:
            matches = await basalam_client.match_titles([p.get("name", "") for p in rows])
        except basalam_client.CatalogUnavailable as e:
            _record(ctx, "suggest_gifts", {"budget_max": budget_max}, False, str(e))
            return CATALOG_DOWN
        candidates = [p for p in basalam_overlay.overlay_many(rows, matches)
                      if p["id"] not in seen and is_available(p)]
        if not candidates:
            # gifts never fall back to the rest of the catalogue — outside this tag
            # nothing is gift-ready (no box, no wrapping)
            _record(ctx, "suggest_gifts", {"budget_max": budget_max}, False, "no match")
            return ("در دستهٔ «مناسب هدیه» با این بودجه گزینهٔ تازه‌ای موجود نیست. صادقانه بگو و"
                    " پیشنهاد بده بودجه را کمی بالاتر ببرد. از بقیهٔ محصولات فروشگاه"
                    " به‌عنوان هدیه پیشنهاد نده.")

        picks = await _pick_by_category(candidates, GIFT_PICKS, matches)
        lines = []
        for product in picks:
            category = (product.get("categories") or [{}])[0].get("name", "")
            lines.append(f"- id={product['id']} | {product['name']} |"
                         f" {persian.fa_amount(product.get('price') or 0)} تومان | دستهٔ {category}")
            ids = _card_ids(product, product["id"])
            # مثل show_product_cards صف می‌شوند، نه ارسال فوری — پیشنهاد هدیه‌ای که
            # پاسخش رد می‌شود نباید کارتش پیش مشتری بماند
            ctx.cards.append({"_id": product["id"], "_ids": ids,
                              "basalam_id": product["basalam_id"],
                              "title": product.get("name", "")})
            ctx.shown_products.extend(ids)
        _record(ctx, "suggest_gifts", {"budget_max": budget_max}, True, f"{len(picks)} gifts")
        out = wrap_data("gift_suggestions", "\n".join(lines)
                        + "\nاین‌ها از دستهٔ «مناسب هدیه» فروشگاه و موجود در غرفهٔ باسلام هستند."
                        + "\nکارت این موارد در باسلام و قبل از متن تو فرستاده می‌شود؛"
                        " show_product_cards را صدا نزن و در متن اسم و قیمتشان را نیاور.")
        ctx.data_outputs.append(out)
        return out

    async def show_product_cards(product_ids: list[int]) -> str:
        """کارت واقعی محصول باسلام — پل آن را با message_type=product می‌فرستد،
        پس مشتری همان کارت آشنای باسلام را با دکمهٔ افزودن به سبد می‌بیند.

        کارت‌ها اینجا فرستاده **نمی‌شوند**: در `ctx.cards` صف می‌شوند و رانر فقط بعد
        از قبول‌شدن پاسخ در validation آن‌ها را می‌فرستد. قبلاً همین‌جا ارسال می‌شدند و
        وقتی پاسخ رد می‌شد، کارت‌های پیش‌نویسِ ردشده پیش مشتری می‌ماندند و بازنویسی
        یک دست کارت دیگر رویشان می‌فرستاد.
        """
        room = max(0, MAX_TOTAL_CARDS - len(ctx.cards))
        wanted = [int(p) for p in (product_ids or [])
                  if int(p) not in ctx.shown_products][:min(MAX_CARDS, room)]
        added: list[str] = []
        evidence: list[str] = []
        for product_id in wanted:
            try:
                product = await _resolve(product_id)
            except (HubUnavailable, basalam_client.CatalogUnavailable):
                continue
            if not product or not is_available(product):
                continue
            ids = _card_ids(product, product_id)
            if any(i in ctx.shown_products for i in ids):
                continue
            ctx.cards.append({"_id": product_id, "_ids": ids,
                              "basalam_id": product["basalam_id"],
                              "title": product.get("name", "")})
            ctx.shown_products.extend(ids)
            added.append(product.get("name", ""))
            evidence.append(_card_evidence(product, product_id))

        _record(ctx, "show_product_cards",
                {"product_ids": wanted, "basalam": [c["basalam_id"] for c in ctx.cards]},
                bool(added), f"{len(ctx.cards)} cards queued")
        if not added:
            return ("کارتی ساخته نشد (قبلاً همین محصول‌ها را نشان داده‌ای، یا در غرفهٔ باسلام"
                    " نیستند/ناموجودند). محصول تکراری دوباره فرستاده نمی‌شود؛ اگر مشتری گزینهٔ"
                    " تازه می‌خواهد، جستجوی دیگری بزن.")
        # این بلوک سندِ همان کارت‌هاست. بدون آن، پاسخی که فقط از روی کارت‌ها نوشته شده
        # به چشم validator بی‌پشتوانه می‌آید و یک جواب درست رد می‌شود.
        out = wrap_data("shown_cards",
                        "کارت این محصول‌ها برای مشتری فرستاده می‌شود:\n" + "\n".join(evidence))
        ctx.data_outputs.append(out)
        return (out + "\n\nکارت‌ها قبل از متن تو به مشتری می‌رسند و اسم و قیمت را خودشان نشان"
                " می‌دهند؛ پس در متن اسم و قیمت را تکرار نکن، فقط یک یا دو جمله بگو چه چیزی"
                " فرستادی و اگر گزینهٔ بیشتری هم بود بگو در پیام بعدی بخواهد تا بفرستی.")

    def record_content_gap(question: str, missing_info: str, category: str = "other") -> str:
        gap_id = save_content_gap(
            question=question, missing_info=missing_info, category=category,
            conversation_id=ctx.conversation_id, product_slug=ctx.product_slug,
            page_url=ctx.page_url,
        )
        ctx.gaps_recorded.append(gap_id)
        _record(ctx, "record_content_gap", {"category": category}, True, question)
        return "ثبت شد. حالا صادقانه به مشتری بگو این اطلاعات هنوز در فروشگاه ثبت نشده است."

    async def check_delivery_time(city_is_qom: bool = False) -> str:
        """زمان ارسال. قم همیشه از انبار پرسیده می‌شود؛ بقیهٔ شهرها قاعدهٔ ساعتی دارند."""
        # A bare «بله خیلی ممنون» once re-opened a week-old delivery thread here and
        # the customer was told we had asked the warehouse. Only a message actually
        # asking about dispatch reaches this tool.
        if ctx.intent not in DELIVERY_INTENTS:
            _record(ctx, "check_delivery_time", {"city_is_qom": city_is_qom}, False,
                    f"blocked: intent={ctx.intent}")
            return ("پیام مشتری سؤال زمان ارسال نیست، پس این ابزار اینجا جواب نمی‌دهد."
                    " بدون آن و بدون هیچ وعده یا ادعای پیگیری، به همان چیزی که مشتری"
                    " نوشته کوتاه جواب بده.")
        if city_is_qom:
            ctx.handoff_requested = "qom_dispatch"
            _record(ctx, "check_delivery_time", {"city_is_qom": True}, True, "warehouse ask")
            return wrap_data("delivery_qom", (
                "مشتری در قم است. سؤال امکان ارسال برای انبار فرستاده شد.\n"
                "به مشتری بگو موضوع را به انبار اطلاع دادی و به‌محض جواب خبر می‌دهی."
                " زمان یا روز مشخصی وعده نده."
            ))

        estimate = iran_calendar.dispatch_estimate()
        _record(ctx, "check_delivery_time", {"city_is_qom": False}, True, estimate["day_label"])
        out = wrap_data("delivery_estimate", (
            f"ساعت الان به وقت ایران: {estimate['now_tehran']}\n"
            f"مهلت ثبت سفارش برای ارسال همان روز: ساعت {estimate['cutoff_hour']}\n"
            f"این سفارش {estimate['day_label']} ارسال می‌شود.\n"
            f"{estimate['note']}\n"
            "روزهای جمعه و تعطیل رسمی ارسال انجام نمی‌شود و در محاسبهٔ بالا لحاظ شده است.\n"
            # «کی ارسال می‌شود» و «چند روزه می‌رسه» دو سؤال‌اند. تا وقتی این خط نبود،
            # مدل برای سؤال دوم همان جملهٔ ارسال را عیناً تکرار می‌کرد.
            f"زمان رسیدن مرسوله جدا از تاریخ ارسال است: {TRANSIT_DAYS}\n"
            "اگر مشتری پرسید «چند روزه می‌رسه»، جواب همین خط است، نه تاریخ ارسال."
        ))
        ctx.data_outputs.append(out)
        return out

    def _restock_out(body: str) -> str:
        """خروجی این ابزار هم سند است و باید در data_outputs بنشیند.

        بدون این، حلقهٔ کنترل ادعای «ناموجود است» را بی‌پشتوانه می‌دید، مدل را به
        جستجوی سطحِ آگهی می‌فرستاد و همان‌جا «موجود» می‌گرفت — پاسخ درست وارونه
        می‌شد و به مشتری می‌گفتیم رایحه‌ای که نداریم موجود است.
        """
        out = wrap_data("restock", body)
        ctx.data_outputs.append(out)
        return out

    async def ask_restock_date(product: str) -> str:
        """«کی شارژ می‌شود؟» — تاریخ شارژ هیچ‌جا ثبت نیست، فقط انبار می‌داند.

        ولی اول باید مطمئن شویم واقعاً ناموجود است: این ابزار یک‌بار محصولِ موجود را
        «ناموجود» جا زد و گفتگو را به اپراتور داد. فروش از دست رفته گران‌ترین خطاست،
        پس ناموجودی اینجا **تأیید** می‌شود، نه فرض.
        """
        found = await basalam_client.search(product, limit=5)
        best = found[0] if found else None

        if best and best.get("available"):
            # موجودیِ آگهی جواب سؤالِ یک مدل نیست: آگهی «کره بدن در پنج رایحه» موجود
            # است چون دو رایحه‌اش هست، ولی «رایحهٔ شکلات» صفر است. فهرست جستجو مدل‌ها
            # را ندارد، پس خود آگهی را می‌گیریم.
            detailed = await basalam_client.product(best["id"]) or best
            variants = detailed.get("variants") or []
            variant = _requested_variant(product, variants)
            if variant and not (variant.get("stock") or 0):
                in_stock = [_variant_name(v) for v in variants if (v.get("stock") or 0)]
                ctx.handoff_requested = RESTOCK
                _record(ctx, "ask_restock_date", {"product": product}, True,
                        f"variant out of stock: {_variant_name(variant)}")
                return _restock_out((
                    # «[ناموجود]» عمداً عین سطرهای جستجو و جزئیات است: حلقهٔ کنترل،
                    # ادعای ناموجودی بدون همین نشانه را «بی‌سند» می‌گیرد و پاسخ درست
                    # را وادار به عقب‌نشینی می‌کند — دقیقاً همان اتفاقی که افتاد.
                    f"وضعیت موجودی: ناموجود — [ناموجود] مدل «{_variant_name(variant)}»"
                    f" از آگهی «{best.get('title')}»"
                    " (خودِ آگهی موجود است، ولی این مدلش نه).\n"
                    + (f"مدل‌های موجود همین آگهی: {'، '.join(in_stock)}\n" if in_stock else "")
                    + f"سؤال زمان شارژ «{product}» برای همکار انسانی فرستاده شد.\n"
                    "به مشتری بگو همین مدل الان ناموجود است، مدل‌های موجود را نام ببر،"
                    " و بگو گفتگو را به همکارت منتقل می‌کنی تا زمان شارژ را بپرسند."
                    " تاریخ از خودت نساز."
                ))
            _record(ctx, "ask_restock_date", {"product": product}, False,
                    f"in stock: {_variant_name(variant) or best.get('title', '')}")
            price = best.get("price_toman")
            return _restock_out((
                f"این محصول همین الان در غرفه **موجود** است: {best.get('title')}"
                + (f" — {price:,} تومان" if price else "")
                + (f"\nمدلی که مشتری پرسید («{_variant_name(variant)}») موجود است."
                   if variant else "") + "\n"
                "پس سؤال زمان شارژ موضوعیت ندارد و گفتگو به همکار منتقل نشد."
                " به مشتری بگو موجود است و می‌تواند همین حالا سفارش بدهد."
                " **هرگز نگو ناموجود است.**"
            ))

        # همان کالا گاهی با آگهی دیگری موجود است؛ فرستادن مشتری به انتظارِ شارژ
        # وقتی جایگزینِ همان لحظه داریم، یعنی از دست دادن یک فروش قطعی
        alternative = next((p for p in found[1:] if p.get("available")), None)
        if alternative:
            _record(ctx, "ask_restock_date", {"product": product}, False,
                    f"alternative listing: {alternative.get('id')}")
            price = alternative.get("price_toman")
            return _restock_out((
                f"وضعیت موجودی: ناموجود ({product})\n"
                f"ولی همین کالا با آگهی دیگری در غرفه موجود است: id={alternative.get('id')}"
                f" | {alternative.get('title')}"
                + (f" — {price:,} تومان" if price else "") + "\n"
                "گفتگو به همکار منتقل نشد. به مشتری بگو آگهی‌ای که فرستاده الان ناموجود"
                " است ولی همین کالا را با آگهی دیگری داریم، و کارت همان را با"
                " show_product_cards بفرست تا بتواند سفارش بدهد."
            ))

        ctx.handoff_requested = RESTOCK
        _record(ctx, "ask_restock_date", {"product": product}, True, product)
        return _restock_out((
            # بررسی شد و واقعاً ناموجود است — همین خط سند ادعای ناموجودی است
            f"وضعیت موجودی: ناموجود ({product})\n"
            f"سؤال زمان شارژ «{product}» برای همکار انسانی فرستاده شد.\n"
            "به مشتری بگو گفتگو را به همکارت منتقل می‌کنی تا زمان شارژ را بپرسند و"
            " نتیجه را همین‌جا خدمتش اعلام کنند. تاریخ یا بازهٔ زمانی از خودت نساز و"
            " نگو اطلاعاتی ثبت نشده."
        ))

    def request_human_handoff(reason: str) -> str:
        ctx.handoff_requested = reason or "agent decision"
        _record(ctx, "request_human_handoff", {"reason": reason}, True)
        return "باشه؛ گفتگو به پشتیبان انسانی منتقل می‌شود. جمله کوتاه و مودبانه‌ای برای اطلاع مشتری بنویس."

    return [
        StructuredTool.from_function(
            coroutine=search_products, name="search_products",
            description="Search products by (possibly misspelled) Persian name, SKU or wording "
                        "from the description («مناسب پوست خشک»), and/or by the shop's own "
                        "tag/category (`tag`: مردانه، زنانه، حراج …) — use `tag` alone for a whole "
                        "group. Each hit comes back with price, stock and a short description "
                        "snippet, so pick from those and only call get_product_details when you "
                        "need specifics (models, expiry, full usage)."),
        StructuredTool.from_function(
            coroutine=get_product_details, name="get_product_details",
            description="Full details (price, stock, variants, attributes, registered description) "
                        "for a product id found via search or the current page."),
        StructuredTool.from_function(
            coroutine=get_store_policy, name="get_store_policy",
            description="Store policy lookup: shipping costs and free-shipping threshold, plus "
                        "store pages by topic (ارسال، مرجوعی، تماس، درباره ما...). Call this for "
                        "any question about هزینه ارسال or شرایط ارسال."),
        StructuredTool.from_function(
            coroutine=show_product_cards, name="show_product_cards",
            description="Show the customer a small card per product (photo, price, add-to-cart "
                        "button). Call it with the ids of every product you recommend or name, "
                        "right before writing the answer. Out-of-stock products are skipped."),
        StructuredTool.from_function(
            coroutine=suggest_gifts, name="suggest_gifts",
            description="Pick 3 in-stock gift products from the shop's «مناسب هدیه» tag for a "
                        "given budget in tomans (budget_max required, budget_min optional). This "
                        "tag is the ONLY source of gift suggestions — never pitch anything else "
                        "as a gift. Skips everything already shown, so calling it again after "
                        "«خوشم نیومد» or a new budget returns different products. Ask the budget "
                        "first."),
        StructuredTool.from_function(
            coroutine=check_delivery_time, name="check_delivery_time",
            description="When will an order placed now be dispatched. ALWAYS ask the customer "
                        "whether they are in Qom or another city first, then call this with "
                        "city_is_qom accordingly (default False when unclear). Handles the 14:00 "
                        "Iran-time cutoff, Fridays and official holidays. Use this for any "
                        "'when will it arrive / do you ship today / if I order now' question — "
                        "these are NOT order-tracking questions."),
        StructuredTool.from_function(
            func=record_content_gap, name="record_content_gap",
            description="Record that a relevant customer question can't be answered because the "
                        "store data lacks the information (e.g. expiry date, usage details). "
                        "Call BEFORE telling the customer the info is unavailable."),
        StructuredTool.from_function(
            coroutine=get_my_order, name="get_my_order",
            description="The latest Basalam order of the customer you are talking to. Takes no "
                        "arguments — identity comes from the conversation itself, so NEVER ask "
                        "for an order number or a phone number. Call this FIRST for anything "
                        "about an existing order («سفارشم کجاست؟»، «کی می‌رسه؟»، «ارسال شد؟»، "
                        "«کد رهگیری»). An empty result means this customer has no order with "
                        "us, so the question is really about shipping or preparation time."),
        StructuredTool.from_function(
            coroutine=send_review_request, name="send_review_request",
            description="Send the customer Basalam's own «ثبت تجربهٔ خرید» cards for the items of "
                        "their delivered order, so they can rate the purchase. Only after "
                        "get_my_order shows the order as delivered."),
        StructuredTool.from_function(
            func=report_delivery_problem, name="report_delivery_problem",
            description="The customer says the parcel never arrived although Basalam marks it as "
                        "delivered. Alerts the shop admins on Telegram; the conversation stays "
                        "with you, so answer the customer yourself afterwards. `details` is one "
                        "short Persian sentence describing what the customer said."),
        StructuredTool.from_function(
            coroutine=ask_restock_date, name="ask_restock_date",
            description="ONLY for «کی شارژ می‌شه؟ / کی موجود می‌شه؟ / کی میاد؟» — a question about "
                        "WHEN something out of stock comes back. Restock dates exist nowhere in "
                        "the product data, only the warehouse knows them, so never answer such a "
                        "question from the description and never record it as a content gap. "
                        "Do NOT call this for «موجوده؟ / دارید؟» (that is search_products) or for "
                        "anything else; the word «موجود» in a product card the customer forwarded "
                        "is not a restock question. `product` is the product (and variant, if "
                        "named) the customer is asking about."),
        StructuredTool.from_function(
            func=request_human_handoff, name="request_human_handoff",
            description="Transfer this conversation to a human operator. Use when the customer "
                        "asks for a human, for sensitive payment/order problems, or when you "
                        "cannot answer safely."),
    ]


def save_content_gap(*, question: str, missing_info: str, category: str,
                     conversation_id: int | None, product_slug: str = "",
                     page_url: str = "", product_name: str = "") -> int:
    """Upsert-style gap recording: similar questions for the same product/category
    increment the existing gap's occurrence count."""
    norm = persian.normalize(question)[:500]
    with db_session() as session:
        existing = (
            session.query(ContentGap)
            .filter(ContentGap.category == category,
                    ContentGap.product_slug == (product_slug or ""),
                    ContentGap.status.in_(["open", "in_progress"]))
            .all()
        )
        match = None
        for gap in existing:
            if gap.question_norm and (gap.question_norm in norm or norm in gap.question_norm):
                match = gap
                break
        if match:
            match.occurrences += 1
            match.last_seen = datetime.now(timezone.utc)
            examples = list(match.example_questions or [])
            if question not in examples:
                examples.append(question)
                match.example_questions = examples[-10:]
            session.flush()
            return match.id
        gap = ContentGap(
            question=question, question_norm=norm, category=category,
            product_slug=product_slug or "", product_name=product_name,
            page_url=page_url, conversation_id=conversation_id,
            missing_info=missing_info, example_questions=[question],
        )
        session.add(gap)
        session.flush()
        return gap.id
