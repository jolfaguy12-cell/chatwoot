"""Webhook signature verification, admin auth, provider-key encryption."""

import hashlib
import hmac
import time

from cryptography.fernet import Fernet, InvalidToken
from fastapi import Header, HTTPException

from app.config import get_settings

CHATWOOT_TS_TOLERANCE_SECONDS = 300


def verify_chatwoot_signature(body: bytes, signature: str | None, timestamp: str | None) -> bool:
    """Chatwoot agent-bot webhooks: X-Chatwoot-Signature = sha256=HMAC(secret, "ts.body")."""
    secret = get_settings().chatwoot_webhook_secret
    if not secret:
        return False
    if not signature or not timestamp:
        return False
    try:
        ts = int(timestamp)
    except ValueError:
        return False
    if abs(time.time() - ts) > CHATWOOT_TS_TOLERANCE_SECONDS:
        return False
    payload = f"{timestamp}.".encode() + body
    expected = "sha256=" + hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


def verify_hub_signature(body: bytes, signature: str | None) -> bool:
    """Hub webhooks: X-BDSK-Signature = hex HMAC-SHA256 of the raw body."""
    secret = get_settings().hub_webhook_secret
    if not secret or not signature:
        return False
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


def verify_telegram_secret(token: str | None) -> bool:
    secret = get_settings().telegram_webhook_secret
    return bool(secret) and hmac.compare_digest(token or "", secret)


async def require_admin(authorization: str = Header(default="")) -> None:
    token = get_settings().admin_token
    provided = authorization.removeprefix("Bearer ").strip()
    if not token or not provided or not hmac.compare_digest(provided, token):
        raise HTTPException(status_code=401, detail="invalid admin token")


def _fernet() -> Fernet:
    return Fernet(get_settings().fernet_key.encode())


def encrypt_secret(plain: str) -> str:
    return _fernet().encrypt(plain.encode()).decode()


def decrypt_secret(token: str) -> str:
    if not token:
        return ""
    try:
        return _fernet().decrypt(token.encode()).decode()
    except InvalidToken:
        return ""
