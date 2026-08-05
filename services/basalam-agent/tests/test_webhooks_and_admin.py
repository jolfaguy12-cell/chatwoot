import hashlib
import hmac
import json
import time


def _post_chatwoot(client, payload: dict, secret: str = "test-chatwoot-secret"):
    body = json.dumps(payload).encode()
    ts = str(int(time.time()))
    sig = "sha256=" + hmac.new(secret.encode(), f"{ts}.".encode() + body,
                               hashlib.sha256).hexdigest()
    return client.post("/webhooks/chatwoot", content=body, headers={
        "Content-Type": "application/json",
        "X-Chatwoot-Timestamp": ts,
        "X-Chatwoot-Signature": sig,
    })


def test_chatwoot_webhook_rejects_bad_signature(client):
    resp = client.post("/webhooks/chatwoot", json={}, headers={
        "X-Chatwoot-Timestamp": str(int(time.time())),
        "X-Chatwoot-Signature": "sha256=bad",
    })
    assert resp.status_code == 401


def test_chatwoot_webhook_dedup(client):
    payload = {
        "event": "message_created", "id": 424242, "message_type": "incoming",
        "private": True, "content": "x",
        "sender": {"id": 1, "type": "contact"},
        "conversation": {"id": 90001, "status": "pending", "custom_attributes": {}},
    }
    first = _post_chatwoot(client, payload)
    second = _post_chatwoot(client, payload)
    assert first.status_code == 200 and first.json() == {"ok": True}
    assert second.json().get("duplicate") is True


def test_chatwoot_webhook_ignores_outgoing(client):
    payload = {"event": "message_created", "id": 424243,
               "message_type": "outgoing", "private": False,
               "sender": {"type": "agent_bot"},
               "conversation": {"id": 90001, "status": "pending"}}
    resp = _post_chatwoot(client, payload)
    assert resp.status_code == 200


def test_hub_webhook_signature(client):
    body = json.dumps({"event": "product.upserted", "entity_id": 1}).encode()
    sig = hmac.new(b"test-hub-secret", body, hashlib.sha256).hexdigest()
    ok = client.post("/webhooks/hub", content=body, headers={"X-BDSK-Signature": sig})
    bad = client.post("/webhooks/hub", content=body, headers={"X-BDSK-Signature": "x"})
    assert ok.status_code == 200
    assert bad.status_code == 401


def test_telegram_webhook_secret(client):
    ok = client.post("/telegram/webhook", json={"update_id": 1},
                     headers={"X-Telegram-Bot-Api-Secret-Token": "test-telegram-secret"})
    bad = client.post("/telegram/webhook", json={"update_id": 1},
                      headers={"X-Telegram-Bot-Api-Secret-Token": "nope"})
    assert ok.status_code == 200
    assert bad.status_code == 401


# --- admin API ---------------------------------------------------------------

AUTH = {"Authorization": "Bearer test-admin-token"}


def test_admin_requires_token(client):
    assert client.get("/admin/v1/settings").status_code == 401
    assert client.get("/admin/v1/settings",
                      headers={"Authorization": "Bearer wrong"}).status_code == 401


def test_admin_settings_roundtrip(client):
    resp = client.patch("/admin/v1/settings", json={"values": {"debounce_seconds": 7}},
                        headers=AUTH)
    assert resp.status_code == 200
    assert resp.json()["debounce_seconds"] == 7
    resp = client.patch("/admin/v1/settings", json={"values": {"debounce_seconds": 4}},
                        headers=AUTH)
    assert resp.json()["debounce_seconds"] == 4


def test_admin_provider_model_role_crud(client):
    provider = client.post("/admin/v1/providers", json={
        "name": "TestProv", "base_url": "http://llm.test/v1", "api_key": "sk-x"},
        headers=AUTH).json()
    assert provider["has_key"] is True

    model = client.post("/admin/v1/models", json={
        "provider_id": provider["id"], "model_name": "test/model-1",
        "input_cost_per_mtok": 1.0, "output_cost_per_mtok": 2.0},
        headers=AUTH).json()

    resp = client.put("/admin/v1/roles/extraction", json={
        "primary_model_id": model["id"], "fallback_model_id": None,
        "traffic_split": [], "enabled": True}, headers=AUTH)
    assert resp.status_code == 200

    # model in use by a role cannot be deleted
    resp = client.delete(f"/admin/v1/models/{model['id']}", headers=AUTH)
    assert resp.status_code == 409

    client.put("/admin/v1/roles/extraction", json={
        "primary_model_id": None, "fallback_model_id": None,
        "traffic_split": [], "enabled": False}, headers=AUTH)
    assert client.delete(f"/admin/v1/models/{model['id']}",
                         headers=AUTH).status_code == 200
    assert client.delete(f"/admin/v1/providers/{provider['id']}",
                         headers=AUTH).status_code == 200


def test_admin_prompt_versioning(client):
    versions = client.get("/admin/v1/prompts/system_main/versions", headers=AUTH).json()
    base_version = max(v["version"] for v in versions)
    created = client.post("/admin/v1/prompts/system_main/versions", json={
        "content": "نسخه آزمایشی", "notes": "test", "activate": True},
        headers=AUTH).json()
    assert created["version"] == base_version + 1
    resp = client.post(f"/admin/v1/prompts/system_main/activate/{base_version}",
                       headers=AUTH)
    assert resp.json()["active_version"] == base_version


def test_selfservice_requires_user_header(client):
    assert client.get("/admin/v1/telegram/self", headers=AUTH).status_code == 400
    resp = client.get("/admin/v1/telegram/self",
                      headers={**AUTH, "X-Chatwoot-User-Id": "55"})
    assert resp.status_code == 200
    assert resp.json()["linked"] is False


def test_evaluation_dimension_validation(client):
    resp = client.post("/admin/v1/responses/999999/evaluations",
                       json={"dimensions": {"bogus_dim": 5}}, headers=AUTH)
    assert resp.status_code == 400
