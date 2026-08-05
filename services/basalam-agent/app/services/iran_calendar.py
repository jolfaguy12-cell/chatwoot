"""تقویم کاری ایران برای وعدهٔ زمان ارسال.

جمعه و تعطیلات رسمی ارسال نداریم. تعطیلات رسمی ایران هر سال جابه‌جا می‌شود و
بخشی‌شان قمری است، پس از سرویس تقویم خوانده و کش می‌شود. اگر سرویس در دسترس
نبود، فقط جمعه تعطیل فرض می‌شود — بدترین حالت این است که وعدهٔ ارسال یک روز
خوش‌بینانه شود، نه اینکه پاسخ‌دهی قطع شود.
"""
import logging
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import httpx
import jdatetime

log = logging.getLogger(__name__)

TEHRAN = ZoneInfo("Asia/Tehran")
CUTOFF_HOUR = 14           # سفارش تا ۲ ظهر همان روز ارسال می‌شود
CALENDAR_API = "https://pnldev.com/api/calender"
LOOKAHEAD_DAYS = 10

_cache: dict[date, bool] = {}


def now() -> datetime:
    return datetime.now(TEHRAN)


def _is_friday(day: date) -> bool:
    return day.weekday() == 4


def is_holiday(day: date) -> bool:
    """جمعه یا تعطیل رسمی."""
    if _is_friday(day):
        return True
    if day in _cache:
        return _cache[day]

    jalali = jdatetime.date.fromgregorian(date=day)
    try:
        resp = httpx.get(
            CALENDAR_API,
            params={"year": jalali.year, "month": jalali.month, "day": jalali.day},
            timeout=6.0,
        )
        resp.raise_for_status()
        holiday = bool((resp.json().get("result") or {}).get("holiday"))
    except (httpx.HTTPError, ValueError) as exc:
        log.warning("calendar lookup failed for %s: %s", day, exc)
        return False

    _cache[day] = holiday
    return holiday


def next_shipping_day(after: date) -> date:
    day = after
    for _ in range(LOOKAHEAD_DAYS):
        day += timedelta(days=1)
        if not is_holiday(day):
            return day
    return day


def fa_weekday(day: date) -> str:
    return jdatetime.date.fromgregorian(date=day).strftime("%A")


def dispatch_estimate() -> dict:
    """چه زمانی سفارشی که همین الان ثبت شود ارسال می‌شود."""
    moment = now()
    today = moment.date()
    before_cutoff = moment.hour < CUTOFF_HOUR

    if not is_holiday(today) and before_cutoff:
        return {
            "ships": "today",
            "day_label": "امروز",
            "cutoff_hour": CUTOFF_HOUR,
            "now_tehran": moment.strftime("%H:%M"),
            "note": "سفارش قبل از ساعت ۱۴ ثبت شود، معمولاً همین امروز ارسال می‌شود.",
        }

    target = next_shipping_day(today)
    tomorrow = today + timedelta(days=1)
    label = "فردا" if target == tomorrow else fa_weekday(target)
    reason = ("امروز تعطیل است" if is_holiday(today)
              else f"ساعت از {CUTOFF_HOUR} گذشته است")
    return {
        "ships": "next_day",
        "day_label": label,
        "cutoff_hour": CUTOFF_HOUR,
        "now_tehran": moment.strftime("%H:%M"),
        "skipped_holidays": (target - today).days > 1,
        "note": f"{reason}، پس ارسال {label} انجام می‌شود.",
    }
