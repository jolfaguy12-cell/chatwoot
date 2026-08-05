"""Admin API: global settings, providers, models, role assignments, prompts."""

import logging
import time

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from app import crud
from app.config import get_settings
from app.db import db_session
from app.models import Model, Prompt, PromptVersion, Provider, RoleAssignment, ROLES
from app.security import decrypt_secret, encrypt_secret

log = logging.getLogger(__name__)
router = APIRouter()


# ---------------------------------------------------------------------------
# settings + health
# ---------------------------------------------------------------------------

@router.get("/settings")
async def get_settings_endpoint():
    with db_session() as session:
        return crud.all_settings(session)


class SettingsPatch(BaseModel):
    values: dict


@router.patch("/settings")
async def patch_settings(body: SettingsPatch, actor: str = ""):
    with db_session() as session:
        before = crud.all_settings(session)
        for key, value in body.values.items():
            crud.set_setting(session, key, value)
        crud.audit(session, "settings_changed", actor=actor or "dashboard", via="dashboard",
                   entity="settings",
                   before={k: before.get(k) for k in body.values},
                   after=body.values)
        return crud.all_settings(session)


@router.get("/health")
async def health():
    from app.services import hub_client, telegram_bot

    out: dict = {"db": True, "ai_enabled": None, "hub": None, "chatwoot": None,
                 "telegram": telegram_bot.get_application() is not None}
    with db_session() as session:
        out["ai_enabled"] = bool(crud.get_setting(session, "ai_enabled"))
    from app.config import get_settings

    settings = get_settings()
    for key, inbox in (("hub", None), ("main_hub", settings.main_inbox_id)):
        if key == "main_hub" and not settings.main_inbox_id:
            continue
        hub_client.use_inbox(inbox)
        try:
            hub = await hub_client.health()
            out[key] = {"ok": bool(hub.get("db_ok")),
                        "mirror_stale_seconds": hub.get("mirror_stale_seconds")}
        except Exception as e:  # noqa: BLE001
            out[key] = {"ok": False, "error": e.__class__.__name__}
    try:
        from app.services import chatwoot_client

        await chatwoot_client.get_conversation(1)
        out["chatwoot"] = {"ok": True}
    except Exception as e:  # noqa: BLE001
        out["chatwoot"] = {"ok": False, "error": str(e)[:120]}
    return out


# ---------------------------------------------------------------------------
# providers
# ---------------------------------------------------------------------------

def _provider_out(p: Provider) -> dict:
    return {"id": p.id, "name": p.name, "kind": p.kind, "base_url": p.base_url,
            "enabled": p.enabled, "has_key": bool(p.api_key_enc)}


class ProviderIn(BaseModel):
    name: str
    base_url: str
    kind: str = "openai_compatible"
    api_key: str | None = None
    enabled: bool = True


@router.get("/providers")
async def list_providers():
    with db_session() as session:
        return [_provider_out(p) for p in session.scalars(select(Provider))]


@router.post("/providers")
async def create_provider(body: ProviderIn, actor: str = ""):
    with db_session() as session:
        provider = Provider(name=body.name, kind=body.kind, base_url=body.base_url,
                            enabled=body.enabled,
                            api_key_enc=encrypt_secret(body.api_key) if body.api_key else "")
        session.add(provider)
        session.flush()
        crud.audit(session, "provider_created", actor=actor or "dashboard", via="dashboard",
                   entity="provider", entity_id=provider.id, after={"name": body.name})
        return _provider_out(provider)


@router.patch("/providers/{provider_id}")
async def update_provider(provider_id: int, body: dict, actor: str = ""):
    with db_session() as session:
        provider = session.get(Provider, provider_id)
        if provider is None:
            raise HTTPException(404)
        before = _provider_out(provider)
        for field in ("name", "kind", "base_url", "enabled"):
            if field in body:
                setattr(provider, field, body[field])
        if body.get("api_key"):
            provider.api_key_enc = encrypt_secret(body["api_key"])
        crud.audit(session, "provider_updated", actor=actor or "dashboard", via="dashboard",
                   entity="provider", entity_id=provider_id,
                   before=before, after=_provider_out(provider))
        return _provider_out(provider)


@router.delete("/providers/{provider_id}")
async def delete_provider(provider_id: int, actor: str = ""):
    with db_session() as session:
        provider = session.get(Provider, provider_id)
        if provider is None:
            raise HTTPException(404)
        in_use = session.scalar(select(Model).where(Model.provider_id == provider_id))
        if in_use:
            raise HTTPException(409, "provider has models — delete them first")
        session.delete(provider)
        crud.audit(session, "provider_deleted", actor=actor or "dashboard", via="dashboard",
                   entity="provider", entity_id=provider_id)
    return {"ok": True}


@router.post("/providers/{provider_id}/test")
async def test_provider(provider_id: int):
    import httpx

    with db_session() as session:
        provider = session.get(Provider, provider_id)
        if provider is None:
            raise HTTPException(404)
        api_key = decrypt_secret(provider.api_key_enc) or get_settings().openrouter_api_key
        base_url = provider.base_url
    start = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(f"{base_url.rstrip('/')}/models",
                                    headers={"Authorization": f"Bearer {api_key}"})
        ok = resp.status_code == 200
        detail = f"HTTP {resp.status_code}"
    except Exception as e:  # noqa: BLE001
        ok, detail = False, e.__class__.__name__
    return {"ok": ok, "detail": detail,
            "latency_ms": int((time.monotonic() - start) * 1000)}


# ---------------------------------------------------------------------------
# models
# ---------------------------------------------------------------------------

def _model_out(m: Model) -> dict:
    return {"id": m.id, "provider_id": m.provider_id, "model_name": m.model_name,
            "label": m.label, "enabled": m.enabled, "supports_audio": m.supports_audio,
            "input_cost_per_mtok": m.input_cost_per_mtok,
            "output_cost_per_mtok": m.output_cost_per_mtok, "params": m.params}


class ModelIn(BaseModel):
    provider_id: int
    model_name: str
    label: str = ""
    enabled: bool = True
    supports_audio: bool = False
    input_cost_per_mtok: float = 0.0
    output_cost_per_mtok: float = 0.0
    params: dict = {}


@router.get("/models")
async def list_models():
    with db_session() as session:
        return [_model_out(m) for m in session.scalars(select(Model))]


@router.post("/models")
async def create_model(body: ModelIn, actor: str = ""):
    with db_session() as session:
        if session.get(Provider, body.provider_id) is None:
            raise HTTPException(400, "unknown provider")
        model = Model(**body.model_dump())
        session.add(model)
        session.flush()
        crud.audit(session, "model_created", actor=actor or "dashboard", via="dashboard",
                   entity="model", entity_id=model.id, after={"model_name": body.model_name})
        return _model_out(model)


@router.patch("/models/{model_id}")
async def update_model(model_id: int, body: dict, actor: str = ""):
    with db_session() as session:
        model = session.get(Model, model_id)
        if model is None:
            raise HTTPException(404)
        before = _model_out(model)
        for field in ("model_name", "label", "enabled", "supports_audio",
                      "input_cost_per_mtok", "output_cost_per_mtok", "params",
                      "provider_id"):
            if field in body:
                setattr(model, field, body[field])
        crud.audit(session, "model_updated", actor=actor or "dashboard", via="dashboard",
                   entity="model", entity_id=model_id, before=before,
                   after=_model_out(model))
        return _model_out(model)


@router.delete("/models/{model_id}")
async def delete_model(model_id: int, actor: str = ""):
    with db_session() as session:
        model = session.get(Model, model_id)
        if model is None:
            raise HTTPException(404)
        used = session.scalar(select(RoleAssignment).where(
            (RoleAssignment.primary_model_id == model_id)
            | (RoleAssignment.fallback_model_id == model_id)))
        if used:
            raise HTTPException(409, f"model is assigned to role '{used.role}'")
        session.delete(model)
        crud.audit(session, "model_deleted", actor=actor or "dashboard", via="dashboard",
                   entity="model", entity_id=model_id)
    return {"ok": True}


@router.post("/models/{model_id}/test")
async def test_model(model_id: int):
    """Round-trip a tiny Persian prompt through the model."""
    from langchain_core.messages import HumanMessage

    from app.agent.llm import _invoke, _ModelSpec  # noqa: PLC2701 — deliberate reuse

    with db_session() as session:
        model = session.get(Model, model_id)
        if model is None:
            raise HTTPException(404)
        provider = session.get(Provider, model.provider_id)
        api_key = decrypt_secret(provider.api_key_enc) or get_settings().openrouter_api_key
        spec = _ModelSpec(model_name=model.model_name, base_url=provider.base_url,
                          api_key=api_key, params=model.params or {},
                          input_cost=model.input_cost_per_mtok,
                          output_cost=model.output_cost_per_mtok, timeout=30)
    try:
        result = await _invoke(spec, [HumanMessage(content="فقط بنویس: سلام")])
        return {"ok": True, "output": str(result.output.content)[:200],
                "latency_ms": result.latency_ms, "cost_usd": result.cost_usd}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "detail": f"{e.__class__.__name__}: {str(e)[:200]}"}


# ---------------------------------------------------------------------------
# roles
# ---------------------------------------------------------------------------

@router.get("/roles")
async def list_roles():
    with db_session() as session:
        rows = {r.role: r for r in session.scalars(select(RoleAssignment))}
        return [
            {"role": role,
             "primary_model_id": rows[role].primary_model_id if role in rows else None,
             "fallback_model_id": rows[role].fallback_model_id if role in rows else None,
             "traffic_split": rows[role].traffic_split if role in rows else [],
             "params": rows[role].params if role in rows else {},
             "enabled": rows[role].enabled if role in rows else False}
            for role in ROLES
        ]


class RoleIn(BaseModel):
    primary_model_id: int | None = None
    fallback_model_id: int | None = None
    traffic_split: list = []
    params: dict = {}
    enabled: bool = True


@router.put("/roles/{role}")
async def put_role(role: str, body: RoleIn, actor: str = ""):
    if role not in ROLES:
        raise HTTPException(400, f"unknown role (valid: {', '.join(ROLES)})")
    with db_session() as session:
        row = session.get(RoleAssignment, role)
        before = {} if row is None else {"primary": row.primary_model_id,
                                         "fallback": row.fallback_model_id}
        if row is None:
            row = RoleAssignment(role=role)
            session.add(row)
        row.primary_model_id = body.primary_model_id
        row.fallback_model_id = body.fallback_model_id
        row.traffic_split = body.traffic_split
        row.params = body.params
        row.enabled = body.enabled
        crud.audit(session, "role_updated", actor=actor or "dashboard", via="dashboard",
                   entity="role", entity_id=role, before=before,
                   after={"primary": body.primary_model_id, "fallback": body.fallback_model_id})
        return {"ok": True}


# ---------------------------------------------------------------------------
# prompts
# ---------------------------------------------------------------------------

@router.get("/prompts")
async def list_prompts():
    with db_session() as session:
        out = []
        for prompt in session.scalars(select(Prompt)):
            content, version = crud.active_prompt(session, prompt.key)
            out.append({"key": prompt.key, "description": prompt.description,
                        "active_version": version, "active_content": content})
        return out


@router.get("/prompts/{key}/versions")
async def prompt_versions(key: str):
    with db_session() as session:
        prompt = session.get(Prompt, key)
        if prompt is None:
            raise HTTPException(404)
        rows = session.scalars(
            select(PromptVersion).where(PromptVersion.prompt_key == key)
            .order_by(PromptVersion.version.desc()))
        return [{"version": r.version, "content": r.content, "notes": r.notes,
                 "created_by": r.created_by, "created_at": r.created_at.isoformat(),
                 "active": r.version == prompt.active_version} for r in rows]


class PromptVersionIn(BaseModel):
    content: str
    notes: str = ""
    activate: bool = True


@router.post("/prompts/{key}/versions")
async def create_prompt_version(key: str, body: PromptVersionIn, actor: str = ""):
    with db_session() as session:
        if session.get(Prompt, key) is None:
            raise HTTPException(404, "unknown prompt key")
        pv = crud.add_prompt_version(session, key, body.content, notes=body.notes,
                                     created_by=actor or "dashboard",
                                     activate=body.activate)
        crud.audit(session, "prompt_version_created", actor=actor or "dashboard",
                   via="dashboard", entity="prompt", entity_id=key,
                   after={"version": pv.version, "activated": body.activate})
        return {"version": pv.version, "activated": body.activate}


@router.post("/prompts/{key}/activate/{version}")
async def activate_prompt(key: str, version: int, actor: str = ""):
    with db_session() as session:
        prompt = session.get(Prompt, key)
        pv = session.scalar(select(PromptVersion).where(
            PromptVersion.prompt_key == key, PromptVersion.version == version))
        if prompt is None or pv is None:
            raise HTTPException(404)
        before = prompt.active_version
        prompt.active_version = version
        crud.audit(session, "prompt_activated", actor=actor or "dashboard", via="dashboard",
                   entity="prompt", entity_id=key,
                   before={"version": before}, after={"version": version})
    return {"ok": True, "active_version": version}
