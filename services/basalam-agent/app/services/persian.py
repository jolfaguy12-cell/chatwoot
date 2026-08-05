"""Persian text utilities (mirror of the Hub's agent_queries helpers)."""

import re

_DIGIT_MAP = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
_CHAR_MAP = str.maketrans({"ي": "ی", "ك": "ک", "ة": "ه", "أ": "ا", "إ": "ا", "‌": " "})


def normalize(text: str | None) -> str:
    if not text:
        return ""
    out = str(text).translate(_DIGIT_MAP).translate(_CHAR_MAP)
    return re.sub(r"\s+", " ", out).strip()


_FA_DIGITS = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")


def fa_digits(value) -> str:
    return str(value).translate(_FA_DIGITS)


def fa_amount(value) -> str:
    """Money the way the store writes it: Persian digits, ٬ thousands mark."""
    return f"{int(value):,}".replace(",", "٬").translate(_FA_DIGITS)


def normalize_phone(raw: str | None) -> str | None:
    """Canonical Iranian mobile core '9XXXXXXXXX' or None."""
    if not raw:
        return None
    digits = re.sub(r"\D", "", str(raw).translate(_DIGIT_MAP))
    for prefix in ("0098", "98", "0"):
        if digits.startswith(prefix) and len(digits) > len(prefix):
            digits = digits[len(prefix):]
            break
    if len(digits) == 10 and digits.startswith("9"):
        return digits
    return None


PRODUCT_URL_RE = re.compile(r"/product/([^/?#]+)")


def slug_from_url(url: str | None) -> str | None:
    from urllib.parse import unquote

    if not url:
        return None
    m = PRODUCT_URL_RE.search(url)
    return unquote(m.group(1)) if m else None


PHONE_IN_TEXT_RE = re.compile(r"(?<!\d)(?:\+?98|0098|0|۰)?[9۹][\d۰-۹٠-٩][\d\s۰-۹٠-٩-]{7,12}")
ORDER_ID_IN_TEXT_RE = re.compile(r"(?<!\d)(\d{3,7})(?!\d)")


def extract_phone(text: str) -> str | None:
    """First valid Iranian mobile found in free text, canonicalized."""
    for m in PHONE_IN_TEXT_RE.finditer(text or ""):
        phone = normalize_phone(re.sub(r"[\s-]", "", m.group(0)))
        if phone:
            return phone
    return None


def extract_order_ids(text: str) -> list[int]:
    """Candidate numeric order ids in free text (3-7 digits, not phone parts)."""
    norm = normalize(text)
    without_phones = PHONE_IN_TEXT_RE.sub(" ", norm)
    return [int(m.group(1)) for m in ORDER_ID_IN_TEXT_RE.finditer(without_phones)][:5]
