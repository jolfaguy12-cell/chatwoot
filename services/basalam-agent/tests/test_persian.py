import pytest

from app.services.persian import (
    extract_order_ids,
    extract_phone,
    normalize,
    normalize_phone,
    slug_from_url,
)


def test_normalize():
    assert normalize("سفارش ۱۲۳") == "سفارش 123"
    assert normalize("كيف") == "کیف"
    assert normalize(None) == ""


@pytest.mark.parametrize("raw,expected", [
    ("09124517893", "9124517893"),
    ("+98 912 451 7893", "9124517893"),
    ("۰۹۱۲۴۵۱۷۸۹۳", "9124517893"),
    ("02188776655", None),
    (None, None),
])
def test_normalize_phone(raw, expected):
    assert normalize_phone(raw) == expected


def test_extract_phone_from_persian_text():
    assert extract_phone("شمارم ۰۹۱۴۸۲۷۲۱۵۲ هست") == "9148272152"
    assert extract_phone("شماره من 0912 451 78 93 است") == "9124517893"
    assert extract_phone("هیچ شماره‌ای ندارم") is None


def test_extract_order_ids_ignores_phones():
    ids = extract_order_ids("سفارش ۲۱۶۱ من کجاست؟ شمارم ۰۹۱۴۸۲۷۲۱۵۲")
    assert 2161 in ids
    assert all(len(str(i)) <= 7 for i in ids)


def test_slug_from_url():
    assert slug_from_url("https://x.ir/product/foo-bar/?a=1") == "foo-bar"
    assert slug_from_url("https://x.ir/product/%d8%ae%d8%b1%db%8c%d8%af/") == "خرید"
    assert slug_from_url("https://x.ir/cart/") is None


def test_extract_longevity_info():
    from app.agent.tools import extract_longevity_info

    product = {
        "attributes": [
            {"name": "ماندگاری", "options": ["۲۴ ماه پس از تولید"]},
            {"name": "رنگ", "options": ["قرمز"]},
        ],
        "short_description": "<p>کیفیت عالی</p>",
        "description": ("<p>وزن خالص: ۱.۲ گرم ✔️ ماندگاری بالا "
                        "📅 تاریخ انقضا: ۲۰۲۹/۰۹ | ساخت ترکیه</p>"),
    }
    info = extract_longevity_info(product)
    assert "24 ماه پس از تولید" in info or "۲۴ ماه پس از تولید" in info
    assert "انقضا" in info
    assert "2029/09" in info
    assert "قرمز" not in info


def test_extract_longevity_info_empty():
    from app.agent.tools import extract_longevity_info

    assert extract_longevity_info({"description": "<p>محصول خوبی است</p>"}) == ""
