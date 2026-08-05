"""Response-log persistence: one row per LLM call + one row for the final
customer-facing outcome of a run."""

from app.db import db_session
from app.models import ResponseLog


def write_run(conversation_id: int | None, message_id: int | None, state: dict) -> int | None:
    """Persist all telemetry from a finished graph run. Returns the id of the
    final (customer-facing) response row."""
    final_id = None
    with db_session() as session:
        for call in state.get("run_logs") or []:
            session.add(ResponseLog(
                conversation_id=conversation_id, message_id=message_id,
                kind=call.get("kind", ""), role=call.get("role", ""),
                model_used=call.get("model_used", ""),
                prompt_key=call.get("prompt_key", ""),
                prompt_version=call.get("prompt_version"),
                input_tokens=call.get("input_tokens", 0),
                output_tokens=call.get("output_tokens", 0),
                cost_usd=call.get("cost_usd", 0.0),
                latency_ms=call.get("latency_ms", 0),
                status="fallback" if call.get("status") == "fallback" else "ok",
                error=call.get("error", ""),
                intent=state.get("intent", ""),
            ))
        total_cost = sum(c.get("cost_usd", 0.0) for c in state.get("run_logs") or [])
        total_latency = sum(c.get("latency_ms", 0) for c in state.get("run_logs") or [])
        models = [c.get("model_used", "") for c in state.get("run_logs") or []
                  if c.get("kind") == "agent" and c.get("model_used")]
        final = ResponseLog(
            conversation_id=conversation_id, message_id=message_id,
            kind=state.get("outcome") or "reply", role="run",
            model_used=models[-1] if models else "",
            input_tokens=sum(c.get("input_tokens", 0) for c in state.get("run_logs") or []),
            output_tokens=sum(c.get("output_tokens", 0) for c in state.get("run_logs") or []),
            cost_usd=total_cost, latency_ms=total_latency,
            status="ok",
            input_text=(state.get("text") or "")[:4000],
            output_text=(state.get("final_text") or "")[:4000],
            tool_calls=state.get("tool_calls") or [],
            validation=state.get("validation") or {},
            intent=state.get("intent", ""),
            product_slug=state.get("resolved_product") or state.get("product_slug", ""),
        )
        session.add(final)
        session.flush()
        final_id = final.id
    return final_id


def write_single(*, kind: str, role: str, conversation_id: int | None = None,
                 message_id: int | None = None, model_used: str = "",
                 input_tokens: int = 0, output_tokens: int = 0, cost_usd: float = 0.0,
                 latency_ms: int = 0, status: str = "ok", error: str = "",
                 input_text: str = "", output_text: str = "") -> int:
    with db_session() as session:
        row = ResponseLog(
            conversation_id=conversation_id, message_id=message_id, kind=kind, role=role,
            model_used=model_used, input_tokens=input_tokens, output_tokens=output_tokens,
            cost_usd=cost_usd, latency_ms=latency_ms, status=status, error=error[:2000],
            input_text=input_text[:4000], output_text=output_text[:4000],
        )
        session.add(row)
        session.flush()
        return row.id
