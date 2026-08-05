"""نگاشت گفتگوی باسلام ↔ چت‌وود و نگهبان تکرار، روی SQLite."""
import sqlite3
import threading

from . import config

_lock = threading.Lock()
_conn = sqlite3.connect(config.DB_PATH, check_same_thread=False)
_conn.row_factory = sqlite3.Row
_conn.executescript(
    """
    CREATE TABLE IF NOT EXISTS chats (
        chat_id         INTEGER PRIMARY KEY,
        conversation_id INTEGER NOT NULL,
        contact_id      INTEGER NOT NULL,
        source_id       TEXT    NOT NULL
    );
    CREATE UNIQUE INDEX IF NOT EXISTS idx_chats_conversation
        ON chats (conversation_id);

    -- user_id شناسهٔ عددی کاربر در باسلام است؛ فیلتر سفارش‌ها با همین انجام
    -- می‌شود، پس هویت مشتری از خود گفتگو می‌آید و چیزی از او پرسیده نمی‌شود.
    CREATE TABLE IF NOT EXISTS contacts (
        hash_id    TEXT PRIMARY KEY,
        contact_id INTEGER NOT NULL,
        user_id    INTEGER
    );

    -- پیام‌هایی که خودمان به باسلام فرستادیم؛ رویداد ۴ آن‌ها باید نادیده برود
    CREATE TABLE IF NOT EXISTS sent_to_basalam (
        message_id INTEGER PRIMARY KEY
    );

    -- کارت ثبت نظر برای هر قلم سفارش فقط یک بار می‌رود
    CREATE TABLE IF NOT EXISTS review_sent (
        item_id INTEGER PRIMARY KEY
    );

    -- ایدمپوتنسی: باسلام وبهوک ناموفق را دوباره می‌فرستد
    CREATE TABLE IF NOT EXISTS seen_events (
        message_id INTEGER PRIMARY KEY
    );
    """
)
_conn.commit()

# ستون تازه روی جدولی که از قبل ساخته شده: CREATE TABLE IF NOT EXISTS اضافه‌اش نمی‌کند
try:
    _conn.execute("ALTER TABLE contacts ADD COLUMN user_id INTEGER")
    _conn.commit()
except sqlite3.OperationalError:
    pass


def _one(sql: str, args=()):
    with _lock:
        row = _conn.execute(sql, args).fetchone()
    return row


def _write(sql: str, args=()) -> bool:
    """True اگر ردیف تازه‌ای درج شد."""
    with _lock:
        cur = _conn.execute(sql, args)
        _conn.commit()
        return cur.rowcount > 0


def get_chat(chat_id: int):
    return _one("SELECT * FROM chats WHERE chat_id = ?", (chat_id,))


def get_chat_by_conversation(conversation_id: int):
    return _one("SELECT * FROM chats WHERE conversation_id = ?", (conversation_id,))


def save_chat(chat_id: int, conversation_id: int, contact_id: int, source_id: str) -> None:
    _write(
        "INSERT OR REPLACE INTO chats (chat_id, conversation_id, contact_id, source_id)"
        " VALUES (?, ?, ?, ?)",
        (chat_id, conversation_id, contact_id, source_id),
    )


def get_contact(hash_id: str):
    row = _one("SELECT contact_id FROM contacts WHERE hash_id = ?", (hash_id,))
    return row["contact_id"] if row else None


def save_contact(hash_id: str, contact_id: int, user_id: int | None = None) -> None:
    _write(
        "INSERT OR REPLACE INTO contacts (hash_id, contact_id, user_id) VALUES (?, ?, ?)",
        (hash_id, contact_id, user_id),
    )


def get_user_id(contact_id: int) -> int | None:
    row = _one("SELECT user_id FROM contacts WHERE contact_id = ?", (contact_id,))
    return row["user_id"] if row else None


def set_user_id(contact_id: int, user_id: int) -> None:
    _write("UPDATE contacts SET user_id = ? WHERE contact_id = ?", (user_id, contact_id))


def claim_review(item_id: int) -> bool:
    """True فقط بار اول — کارت ثبت نظر نباید دو بار برای یک قلم برود."""
    return _write("INSERT OR IGNORE INTO review_sent (item_id) VALUES (?)", (item_id,))


def mark_sent_to_basalam(message_id: int) -> None:
    _write("INSERT OR IGNORE INTO sent_to_basalam (message_id) VALUES (?)", (message_id,))


def was_sent_by_us(message_id: int) -> bool:
    return _one("SELECT 1 FROM sent_to_basalam WHERE message_id = ?", (message_id,)) is not None


def claim_event(message_id: int) -> bool:
    """اولین باری که این پیام دیده می‌شود True، دفعات بعد False."""
    return _write("INSERT OR IGNORE INTO seen_events (message_id) VALUES (?)", (message_id,))
