import hashlib
import hmac
import json
import time

from app.security import (
    decrypt_secret,
    encrypt_secret,
    verify_chatwoot_signature,
    verify_hub_signature,
    verify_telegram_secret,
)

BODY = json.dumps({"event": "message_created"}).encode()


def _sign_chatwoot(body: bytes, ts: str, secret: str = "test-chatwoot-secret") -> str:
    return "sha256=" + hmac.new(secret.encode(), f"{ts}.".encode() + body,
                                hashlib.sha256).hexdigest()


def test_chatwoot_signature_valid():
    ts = str(int(time.time()))
    assert verify_chatwoot_signature(BODY, _sign_chatwoot(BODY, ts), ts)


def test_chatwoot_signature_wrong_secret():
    ts = str(int(time.time()))
    assert not verify_chatwoot_signature(BODY, _sign_chatwoot(BODY, ts, "other"), ts)


def test_chatwoot_signature_stale_timestamp():
    ts = str(int(time.time()) - 3600)
    assert not verify_chatwoot_signature(BODY, _sign_chatwoot(BODY, ts), ts)


def test_chatwoot_signature_missing():
    assert not verify_chatwoot_signature(BODY, None, None)


def test_hub_signature():
    sig = hmac.new(b"test-hub-secret", BODY, hashlib.sha256).hexdigest()
    assert verify_hub_signature(BODY, sig)
    assert not verify_hub_signature(BODY, "deadbeef")
    assert not verify_hub_signature(BODY, None)


def test_telegram_secret():
    assert verify_telegram_secret("test-telegram-secret")
    assert not verify_telegram_secret("wrong")
    assert not verify_telegram_secret(None)


def test_fernet_roundtrip():
    token = encrypt_secret("sk-super-secret")
    assert token != "sk-super-secret"
    assert decrypt_secret(token) == "sk-super-secret"
    assert decrypt_secret("garbage") == ""
    assert decrypt_secret("") == ""
