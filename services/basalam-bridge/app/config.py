"""تنظیمات از محیط. سرویس بدون این مقادیر بالا نمی‌آید."""
import os


def _int(name: str, default: int = 0) -> int:
    return int(os.environ.get(name) or default)


HOST = os.environ.get("BDSK_BSLM_HOST", "127.0.0.1")
PORT = _int("BDSK_BSLM_PORT", 8103)
DB_PATH = os.environ["BDSK_BSLM_DB_PATH"]
LOG_LEVEL = os.environ.get("BDSK_BSLM_LOG_LEVEL", "INFO")

# باسلام
BSLM_API = os.environ["BDSK_BSLM_API_BASE_URL"].rstrip("/")
BSLM_TOKEN = os.environ["BDSK_BSLM_ACCESS_TOKEN"]
SELF_USER_ID = _int("BDSK_BSLM_SELF_USER_ID")
VENDOR_ID = _int("BDSK_BSLM_VENDOR_ID")
WEBHOOK_SECRET = os.environ["BDSK_BSLM_WEBHOOK_SECRET"]

# چت‌وود
CW_API = os.environ["BDSK_BSLM_CHATWOOT_BASE_URL"].rstrip("/")
CW_ACCOUNT_ID = _int("BDSK_BSLM_CHATWOOT_ACCOUNT_ID")
CW_INBOX_ID = _int("BDSK_BSLM_CHATWOOT_INBOX_ID")
CW_TOKEN = os.environ["BDSK_BSLM_CHATWOOT_USER_TOKEN"]
CW_WEBHOOK_SECRET = os.environ["BDSK_BSLM_CHATWOOT_WEBHOOK_SECRET"]

# پیام‌هایی که ما در چت‌وود می‌سازیم با این پیشوند علامت می‌خورند تا
# وبهوک برگشتی چت‌وود دوباره آن‌ها را به باسلام نفرستد.
SOURCE_PREFIX = "bslm:"
PRODUCT_URL = "https://basalam.com/p/{id}"
