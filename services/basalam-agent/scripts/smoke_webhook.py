"""Signed synthetic Chatwoot webhook smoke test.

Verifies: bad signature → 401, good signature → 200, duplicate replay → flagged.
Uses a private message so no agent run is triggered.

Run:  .venv/bin/python -m scripts.smoke_webhook
"""

import hashlib
import hmac
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import httpx  # noqa: E402

from app.config import get_settings  # noqa: E402


def main() -> int:
    settings = get_settings()
    url = f"http://{settings.host}:{settings.port}/webhooks/chatwoot"
    payload = {
        "event": "message_created",
        "id": int(time.time()),  # unique per run so dedup state doesn't leak
        "message_type": "incoming",
        "private": True,
        "content": "smoke test",
        "sender": {"id": 1, "type": "contact", "name": "Smoke"},
        "conversation": {"id": 999999, "status": "pending", "custom_attributes": {}},
    }
    body = json.dumps(payload).encode()
    ts = str(int(time.time()))
    sig = "sha256=" + hmac.new(settings.chatwoot_webhook_secret.encode(),
                               f"{ts}.".encode() + body, hashlib.sha256).hexdigest()

    bad = httpx.post(url, content=body, headers={
        "Content-Type": "application/json",
        "X-Chatwoot-Timestamp": ts, "X-Chatwoot-Signature": "sha256=deadbeef"})
    print(f"bad signature   -> {bad.status_code} (expect 401)")

    stale_ts = str(int(time.time()) - 3600)
    stale_sig = "sha256=" + hmac.new(settings.chatwoot_webhook_secret.encode(),
                                     f"{stale_ts}.".encode() + body,
                                     hashlib.sha256).hexdigest()
    stale = httpx.post(url, content=body, headers={
        "Content-Type": "application/json",
        "X-Chatwoot-Timestamp": stale_ts, "X-Chatwoot-Signature": stale_sig})
    print(f"stale timestamp -> {stale.status_code} (expect 401)")

    ok = httpx.post(url, content=body, headers={
        "Content-Type": "application/json",
        "X-Chatwoot-Timestamp": ts, "X-Chatwoot-Signature": sig})
    print(f"good signature  -> {ok.status_code} {ok.json()} (expect 200)")

    dup = httpx.post(url, content=body, headers={
        "Content-Type": "application/json",
        "X-Chatwoot-Timestamp": ts, "X-Chatwoot-Signature": sig})
    print(f"duplicate       -> {dup.status_code} {dup.json()} (expect duplicate: true)")

    passed = (bad.status_code == 401 and stale.status_code == 401
              and ok.status_code == 200 and dup.json().get("duplicate") is True)
    print("PASS" if passed else "FAIL")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
