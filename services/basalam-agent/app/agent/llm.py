"""Role-based LLM invocation.

Every call resolves role → model → provider from the DB at call time, so
admins can switch models/providers/traffic splits with no restart. Primary
failure/timeout falls back once to the role's fallback model; total failure
raises LLMFailure (callers turn that into a human handoff, never silence).
"""

import asyncio
import logging
import random
import time
from dataclasses import dataclass, field
from typing import Any

from langchain_openai import ChatOpenAI

from app import crud
from app.config import get_settings
from app.db import db_session
from app.security import decrypt_secret

log = logging.getLogger(__name__)


class LLMFailure(Exception):
    pass


class LLMTruncated(LLMFailure):
    """The provider returned a *partial* completion instead of an error.

    OpenRouter answers 200 with `finish_reason: "error"`, zeroed usage and
    whatever text the upstream model had produced when the stream died. Left
    unchecked that half-sentence goes straight to the customer («…و هم ما
    بتونیم بهترین») or breaks structured parsing. It is transient, so the same
    model is retried once before falling back.
    """


class LLMQuotaExceeded(LLMFailure):
    """Provider refused because of billing/rate limits, not a transient fault.

    Retrying or falling back does not help within the same window, so callers
    tell the customer plainly and hand the conversation to a human.
    """


# OpenRouter/OpenAI-compatible providers signal exhaustion in the error text.
_QUOTA_MARKERS = (
    "429", "rate limit", "rate_limit", "ratelimit", "quota", "insufficient",
    "credits", "billing", "payment required", "402", "exceeded your",
    "too many requests",
)


def is_quota_error(message: str) -> bool:
    lowered = (message or "").lower()
    return any(marker in lowered for marker in _QUOTA_MARKERS)


@dataclass
class LLMResult:
    output: Any = None            # AIMessage, or parsed pydantic when structured
    raw: Any = None               # underlying AIMessage (structured calls)
    model_used: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    latency_ms: int = 0
    status: str = "ok"            # ok | fallback
    errors: list[str] = field(default_factory=list)


@dataclass
class _ModelSpec:
    model_name: str
    base_url: str
    api_key: str
    params: dict
    input_cost: float
    output_cost: float
    timeout: float
    supports_audio: bool = False


def _resolve_role(role: str) -> tuple[_ModelSpec | None, _ModelSpec | None]:
    """Return (primary, fallback) specs for a role, honoring traffic split."""
    settings = get_settings()
    with db_session() as session:
        assignment = crud.get_role(session, role)
        if assignment is None or not assignment.enabled:
            return None, None
        primary_id = assignment.primary_model_id
        if assignment.traffic_split:
            candidates = [s for s in assignment.traffic_split
                          if s.get("model_id") and s.get("pct", 0) > 0]
            if candidates:
                total = sum(s["pct"] for s in candidates)
                remainder = max(0, 100 - total)
                pick = random.uniform(0, total + remainder)
                acc = 0.0
                for s in candidates:
                    acc += s["pct"]
                    if pick <= acc:
                        primary_id = s["model_id"]
                        break

        timeout_key = "main_timeout_seconds" if role in ("main_response", "stt") \
            else "aux_timeout_seconds"
        timeout = float(crud.get_setting(session, timeout_key) or 40)

        def spec(model_id):
            model = crud.get_model(session, model_id)
            if model is None or not model.enabled:
                return None
            provider = crud.get_provider(session, model.provider_id)
            if provider is None or not provider.enabled:
                return None
            api_key = decrypt_secret(provider.api_key_enc) or settings.openrouter_api_key
            merged = dict(assignment.params or {})
            merged.update(model.params or {})
            return _ModelSpec(
                model_name=model.model_name, base_url=provider.base_url,
                api_key=api_key, params=merged,
                input_cost=model.input_cost_per_mtok, output_cost=model.output_cost_per_mtok,
                timeout=merged.get("timeout_seconds") or timeout,
                supports_audio=model.supports_audio,
            )

        return spec(primary_id), spec(assignment.fallback_model_id)


def _chat(spec: _ModelSpec) -> ChatOpenAI:
    params = spec.params or {}
    return ChatOpenAI(
        model=spec.model_name,
        base_url=spec.base_url,
        api_key=spec.api_key,
        temperature=params.get("temperature", 0.2),
        max_tokens=params.get("max_tokens"),
        timeout=spec.timeout,
        max_retries=1,
        default_headers={
            "HTTP-Referer": "https://support.behdashtik.ir",
            "X-Title": "Behdashtik AI Support",
        },
    )


# A completion that stopped for any of these is unusable: the text is cut mid
# sentence and no `max_tokens` of ours is involved.
BROKEN_FINISH_REASONS = ("error", "length", "content_filter")


def _check_complete(message) -> None:
    reason = (getattr(message, "response_metadata", {}) or {}).get("finish_reason") or ""
    if reason in BROKEN_FINISH_REASONS:
        raise LLMTruncated(f"partial completion (finish_reason={reason})")


def _usage(message) -> tuple[int, int]:
    usage = getattr(message, "usage_metadata", None) or {}
    if usage:
        return usage.get("input_tokens", 0), usage.get("output_tokens", 0)
    # `or {}` on both hops: the attributes exist but come back None on some
    # OpenRouter responses, and an AttributeError here was surfacing to customers
    # as «مشکلی در پاسخگویی خودکار پیش اومده» plus a needless handoff
    meta = (getattr(message, "response_metadata", None) or {}).get("token_usage") or {}
    return meta.get("prompt_tokens", 0), meta.get("completion_tokens", 0)


async def _invoke(spec: _ModelSpec, messages, *, structured=None, tools=None) -> LLMResult:
    llm = _chat(spec)
    if tools:
        llm = llm.bind_tools(tools)
    runnable = llm
    if structured is not None:
        runnable = llm.with_structured_output(structured, include_raw=True)
    start = time.monotonic()
    out = await asyncio.wait_for(runnable.ainvoke(messages), timeout=spec.timeout + 5)
    latency = int((time.monotonic() - start) * 1000)
    if structured is not None:
        raw = out.get("raw")
        _check_complete(raw)
        parsed = out.get("parsed")
        if parsed is None:
            raise ValueError(f"structured output parse failed: {out.get('parsing_error')}")
        in_tok, out_tok = _usage(raw)
        result = LLMResult(output=parsed, raw=raw)
    else:
        _check_complete(out)
        in_tok, out_tok = _usage(out)
        result = LLMResult(output=out, raw=out)
    result.model_used = spec.model_name
    result.input_tokens = in_tok
    result.output_tokens = out_tok
    result.cost_usd = (in_tok * spec.input_cost + out_tok * spec.output_cost) / 1_000_000
    result.latency_ms = latency
    return result


async def run_role(role: str, messages, *, structured=None, tools=None) -> LLMResult:
    """Invoke the role's primary model, falling back once on failure."""
    primary, fallback = _resolve_role(role)
    if primary is None and fallback is None:
        raise LLMFailure(f"no enabled model configured for role '{role}'")
    errors: list[str] = []
    for i, spec in enumerate(s for s in (primary, fallback) if s is not None):
        # a partial completion is an upstream hiccup, not a bad model — one more
        # try on the same model is cheaper and closer to what the role expects
        for attempt in range(2):
            try:
                result = await _invoke(spec, messages, structured=structured, tools=tools)
                if i > 0 or errors:
                    result.status = "fallback"
                result.errors = errors
                return result
            except LLMTruncated as e:
                errors.append(f"{spec.model_name}: {e}")
                log.warning("role %s model %s returned a partial completion (attempt %s)",
                            role, spec.model_name, attempt + 1)
            except Exception as e:  # noqa: BLE001 — collect and try fallback
                errors.append(f"{spec.model_name}: {e.__class__.__name__}: {str(e)[:200]}")
                # A malformed structured payload is the expected upstream hiccup and
                # is retried below, so it stays a one-liner. Anything else gets its
                # traceback: a bare «AttributeError» says a model call broke but not
                # where, and one of those hid a real bug in `_usage` for weeks.
                expected = e.__class__.__name__ in ("ValidationError", "ValueError")
                log.warning("role %s model %s failed: %s", role, spec.model_name, errors[-1],
                            exc_info=not expected)
                # the same upstream hiccup also arrives as a malformed payload
                # (AttributeError / structured-parse ValidationError); one more
                # try on this model beats jumping to a different one. A quota
                # error will not clear within the window, so it fails fast.
                if attempt == 0 and not is_quota_error(str(e)):
                    continue
                break
    summary = f"all models failed for role '{role}': {'; '.join(errors)}"
    if any(is_quota_error(err) for err in errors):
        raise LLMQuotaExceeded(summary)
    raise LLMFailure(summary)
