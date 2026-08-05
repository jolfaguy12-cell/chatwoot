"""Admin API: content gaps, response log + evaluations, stats, handoffs,
operators, audit log."""

import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import Float, case, cast, desc, func, select

from app import crud
from app.db import db_session
from app.models import (
    AuditLog,
    ContentGap,
    Conversation,
    Evaluation,
    Handoff,
    ResponseLog,
    TelegramOperator,
)

log = logging.getLogger(__name__)
router = APIRouter()


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


# ---------------------------------------------------------------------------
# content gaps
# ---------------------------------------------------------------------------

GAP_SORTS = {
    "occurrences": ContentGap.occurrences, "priority": ContentGap.priority,
    "last_seen": ContentGap.last_seen, "first_seen": ContentGap.first_seen,
}


def _gap_out(g: ContentGap) -> dict:
    return {
        "id": g.id, "question": g.question, "category": g.category,
        "product_slug": g.product_slug, "product_name": g.product_name,
        "page_url": g.page_url, "conversation_id": g.conversation_id,
        "missing_info": g.missing_info, "status": g.status, "priority": g.priority,
        "owner": g.owner, "notes": g.notes, "occurrences": g.occurrences,
        "example_questions": g.example_questions, "last_retest": g.last_retest,
        "first_seen": _iso(g.first_seen), "last_seen": _iso(g.last_seen),
    }


@router.get("/content_gaps")
async def list_gaps(q: str = "", status: str = "", category: str = "",
                    product: str = "", sort: str = "last_seen", dir: str = "desc",
                    page: int = 1, per_page: int = Query(default=25, le=100)):
    with db_session() as session:
        stmt = select(ContentGap)
        if q:
            like = f"%{q}%"
            stmt = stmt.where(ContentGap.question.like(like)
                              | ContentGap.product_name.like(like)
                              | ContentGap.notes.like(like))
        if status:
            stmt = stmt.where(ContentGap.status == status)
        if category:
            stmt = stmt.where(ContentGap.category == category)
        if product:
            stmt = stmt.where(ContentGap.product_slug.like(f"%{product}%"))
        total = session.scalar(select(func.count()).select_from(stmt.subquery()))
        col = GAP_SORTS.get(sort, ContentGap.last_seen)
        stmt = stmt.order_by(desc(col) if dir != "asc" else col)
        rows = session.scalars(stmt.offset((page - 1) * per_page).limit(per_page))
        return {"data": [_gap_out(g) for g in rows], "total": total,
                "page": page, "per_page": per_page}


class GapPatch(BaseModel):
    status: str | None = None
    priority: int | None = None
    owner: str | None = None
    notes: str | None = None
    category: str | None = None


@router.patch("/content_gaps/{gap_id}")
async def patch_gap(gap_id: int, body: GapPatch, actor: str = ""):
    with db_session() as session:
        gap = session.get(ContentGap, gap_id)
        if gap is None:
            raise HTTPException(404)
        before = {k: getattr(gap, k) for k in ("status", "priority", "owner")}
        for field, value in body.model_dump(exclude_none=True).items():
            setattr(gap, field, value)
        crud.audit(session, "content_gap_updated", actor=actor or "dashboard",
                   via="dashboard", entity="content_gap", entity_id=gap_id,
                   before=before, after=body.model_dump(exclude_none=True))
        return _gap_out(gap)


# ---------------------------------------------------------------------------
# responses + evaluations
# ---------------------------------------------------------------------------

RESPONSE_SORTS = {
    "created_at": ResponseLog.created_at, "cost_usd": ResponseLog.cost_usd,
    "latency_ms": ResponseLog.latency_ms,
}

EVAL_DIMENSIONS = (
    "correctness", "relevance", "completeness", "persian_quality", "tone",
    "product_identification", "hub_data_usage", "tool_usage", "no_fabrication",
    "scope_handling", "handoff_decision",
)


def _response_out(r: ResponseLog, evals: list[Evaluation] | None = None) -> dict:
    out = {
        "id": r.id, "conversation_id": r.conversation_id, "message_id": r.message_id,
        "kind": r.kind, "role": r.role, "model_used": r.model_used,
        "prompt_key": r.prompt_key, "prompt_version": r.prompt_version,
        "input_tokens": r.input_tokens, "output_tokens": r.output_tokens,
        "cost_usd": r.cost_usd, "latency_ms": r.latency_ms, "status": r.status,
        "error": r.error, "input_text": r.input_text, "output_text": r.output_text,
        "tool_calls": r.tool_calls, "validation": r.validation, "intent": r.intent,
        "product_slug": r.product_slug, "created_at": _iso(r.created_at),
    }
    if evals is not None:
        out["evaluations"] = [
            {"id": e.id, "rater_name": e.rater_name, "auto": e.auto,
             "dimensions": e.dimensions, "comment": e.comment,
             "corrected_response": e.corrected_response, "created_at": _iso(e.created_at)}
            for e in evals
        ]
    return out


@router.get("/responses")
async def list_responses(conversation_id: int | None = None, model: str = "",
                         kind: str = "", status: str = "", intent: str = "",
                         runs_only: bool = True, has_eval: bool | None = None,
                         date_from: str = "", date_to: str = "",
                         sort: str = "created_at", dir: str = "desc",
                         page: int = 1, per_page: int = Query(default=25, le=100)):
    with db_session() as session:
        stmt = select(ResponseLog)
        if runs_only:
            stmt = stmt.where(ResponseLog.role.in_(["run", "stt"]))
        if conversation_id:
            stmt = stmt.where(ResponseLog.conversation_id == conversation_id)
        if model:
            stmt = stmt.where(ResponseLog.model_used.like(f"%{model}%"))
        if kind:
            stmt = stmt.where(ResponseLog.kind == kind)
        if status:
            stmt = stmt.where(ResponseLog.status == status)
        if intent:
            stmt = stmt.where(ResponseLog.intent == intent)
        if date_from:
            stmt = stmt.where(ResponseLog.created_at >= datetime.fromisoformat(date_from))
        if date_to:
            stmt = stmt.where(ResponseLog.created_at <= datetime.fromisoformat(date_to))
        if has_eval is not None:
            eval_ids = select(Evaluation.response_id)
            stmt = stmt.where(ResponseLog.id.in_(eval_ids) if has_eval
                              else ResponseLog.id.not_in(eval_ids))
        total = session.scalar(select(func.count()).select_from(stmt.subquery()))
        col = RESPONSE_SORTS.get(sort, ResponseLog.created_at)
        stmt = stmt.order_by(desc(col) if dir != "asc" else col)
        rows = list(session.scalars(stmt.offset((page - 1) * per_page).limit(per_page)))
        return {"data": [_response_out(r) for r in rows], "total": total,
                "page": page, "per_page": per_page,
                "eval_dimensions": list(EVAL_DIMENSIONS)}


@router.get("/responses/{response_id}")
async def get_response(response_id: int):
    with db_session() as session:
        row = session.get(ResponseLog, response_id)
        if row is None:
            raise HTTPException(404)
        evals = list(session.scalars(
            select(Evaluation).where(Evaluation.response_id == response_id)))
        detail = _response_out(row, evals)
        # include the per-call breakdown of the same run (same conversation+message)
        if row.role == "run":
            calls = session.scalars(
                select(ResponseLog).where(
                    ResponseLog.conversation_id == row.conversation_id,
                    ResponseLog.message_id == row.message_id,
                    ResponseLog.role != "run")
                .order_by(ResponseLog.id))
            detail["calls"] = [_response_out(c) for c in calls]
        return detail


class EvaluationIn(BaseModel):
    dimensions: dict = {}
    comment: str = ""
    corrected_response: str = ""
    rater_user_id: int | None = None
    rater_name: str = ""


@router.post("/responses/{response_id}/evaluations")
async def create_evaluation(response_id: int, body: EvaluationIn):
    bad = [k for k in body.dimensions if k not in EVAL_DIMENSIONS]
    if bad:
        raise HTTPException(400, f"unknown dimensions: {bad}")
    with db_session() as session:
        if session.get(ResponseLog, response_id) is None:
            raise HTTPException(404)
        evaluation = Evaluation(
            response_id=response_id, rater_user_id=body.rater_user_id,
            rater_name=body.rater_name, dimensions=body.dimensions,
            comment=body.comment, corrected_response=body.corrected_response,
        )
        session.add(evaluation)
        session.flush()
        crud.audit(session, "evaluation_created", actor=body.rater_name or "dashboard",
                   via="dashboard", entity="response", entity_id=response_id)
        return {"id": evaluation.id}


# ---------------------------------------------------------------------------
# stats
# ---------------------------------------------------------------------------

@router.get("/stats/usage")
async def stats_usage(days: int = 30, group_by: str = "day"):
    since = datetime.now(timezone.utc) - timedelta(days=days)
    with db_session() as session:
        if group_by == "model":
            key = ResponseLog.model_used
        elif group_by == "kind":
            key = ResponseLog.kind
        else:
            key = func.date(ResponseLog.created_at)
        rows = session.execute(
            select(
                key.label("key"),
                func.count().label("requests"),
                func.sum(ResponseLog.input_tokens).label("input_tokens"),
                func.sum(ResponseLog.output_tokens).label("output_tokens"),
                func.sum(ResponseLog.cost_usd).label("cost_usd"),
                func.avg(ResponseLog.latency_ms).label("avg_latency_ms"),
                func.sum(case((ResponseLog.status != "ok", 1), else_=0)).label("errors"),
            )
            .where(ResponseLog.created_at >= since, ResponseLog.role != "run")
            .group_by("key").order_by("key")
        ).all()
        totals = session.execute(
            select(
                func.count().label("runs"),
                func.sum(ResponseLog.cost_usd).label("cost_usd"),
                func.avg(ResponseLog.latency_ms).label("avg_latency_ms"),
                func.sum(case((ResponseLog.kind == "handoff", 1), else_=0)).label("handoffs"),
            ).where(ResponseLog.created_at >= since, ResponseLog.role == "run")
        ).one()
        return {
            "rows": [dict(r._mapping) for r in rows],
            "totals": {"runs": totals.runs, "cost_usd": totals.cost_usd or 0,
                       "avg_latency_ms": totals.avg_latency_ms or 0,
                       "handoffs": totals.handoffs or 0},
        }


@router.get("/stats/models/compare")
async def stats_compare(days: int = 30):
    """Per-model quality/cost/latency, joining human+auto evaluation averages."""
    since = datetime.now(timezone.utc) - timedelta(days=days)
    with db_session() as session:
        rows = session.execute(
            select(
                ResponseLog.model_used.label("model"),
                func.count().label("runs"),
                func.sum(ResponseLog.cost_usd).label("cost_usd"),
                func.avg(ResponseLog.latency_ms).label("avg_latency_ms"),
                func.sum(case((ResponseLog.kind == "handoff", 1), else_=0)).label("handoffs"),
            )
            .where(ResponseLog.created_at >= since, ResponseLog.role == "run",
                   ResponseLog.model_used != "")
            .group_by(ResponseLog.model_used)
        ).all()
        out = []
        for r in rows:
            evals = session.execute(
                select(Evaluation.dimensions)
                .join(ResponseLog, Evaluation.response_id == ResponseLog.id)
                .where(ResponseLog.model_used == r.model,
                       ResponseLog.created_at >= since)
            ).scalars().all()
            scores = [v for d in evals for v in d.values()
                      if isinstance(v, (int, float))]
            out.append({**dict(r._mapping),
                        "eval_count": len(evals),
                        "avg_score": round(sum(scores) / len(scores), 2) if scores else None})
        return out


# ---------------------------------------------------------------------------
# handoffs / conversations
# ---------------------------------------------------------------------------

def _handoff_out(h: Handoff, session) -> dict:
    operator = session.get(TelegramOperator, h.claimed_by) if h.claimed_by else None
    return {"id": h.id, "conversation_id": h.conversation_id, "reason": h.reason,
            "reason_kind": h.reason_kind, "status": h.status,
            "claimed_by": operator.chatwoot_user_name if operator else None,
            "claimed_via": h.claimed_via, "context": h.context,
            "created_at": _iso(h.created_at), "claimed_at": _iso(h.claimed_at),
            "closed_at": _iso(h.closed_at)}


@router.get("/handoffs")
async def list_handoffs(status: str = "", page: int = 1,
                        per_page: int = Query(default=25, le=100)):
    with db_session() as session:
        stmt = select(Handoff)
        if status:
            stmt = stmt.where(Handoff.status == status)
        total = session.scalar(select(func.count()).select_from(stmt.subquery()))
        rows = session.scalars(stmt.order_by(desc(Handoff.id))
                               .offset((page - 1) * per_page).limit(per_page))
        return {"data": [_handoff_out(h, session) for h in rows], "total": total}


@router.post("/handoffs/{handoff_id}/return_to_ai")
async def handoff_return(handoff_id: int, actor: str = ""):
    from app.services import handoff as handoff_service

    with db_session() as session:
        handoff = session.get(Handoff, handoff_id)
        if handoff is None:
            raise HTTPException(404)
        conversation_id = handoff.conversation_id
    ok = await handoff_service.return_to_ai(conversation_id,
                                            actor=actor or "dashboard", via="dashboard")
    return {"ok": ok}


@router.post("/conversations/{conversation_id}/pause")
async def pause_conversation(conversation_id: int, actor: str = ""):
    with db_session() as session:
        conv = crud.get_conversation(session, conversation_id)
        conv.mode = "disabled"
        crud.audit(session, "conversation_paused", actor=actor or "dashboard",
                   via="dashboard", entity="conversation", entity_id=conversation_id)
    return {"ok": True}


@router.post("/conversations/{conversation_id}/resume")
async def resume_conversation(conversation_id: int, actor: str = ""):
    with db_session() as session:
        conv = crud.get_conversation(session, conversation_id)
        conv.mode = "ai"
        crud.audit(session, "conversation_resumed", actor=actor or "dashboard",
                   via="dashboard", entity="conversation", entity_id=conversation_id)
    return {"ok": True}


# ---------------------------------------------------------------------------
# operators
# ---------------------------------------------------------------------------

def _operator_out(op: TelegramOperator) -> dict:
    return {"id": op.id, "chatwoot_user_id": op.chatwoot_user_id,
            "chatwoot_user_name": op.chatwoot_user_name,
            "telegram_username": op.telegram_username, "pref": op.pref,
            "active": op.active, "linked_at": _iso(op.linked_at)}


@router.get("/operators")
async def list_operators():
    with db_session() as session:
        return [_operator_out(op) for op in session.scalars(select(TelegramOperator))]


@router.patch("/operators/{operator_id}")
async def patch_operator(operator_id: int, body: dict, actor: str = ""):
    with db_session() as session:
        op = session.get(TelegramOperator, operator_id)
        if op is None:
            raise HTTPException(404)
        if "pref" in body:
            if body["pref"] not in ("dashboard", "telegram", "both", "disabled"):
                raise HTTPException(400, "invalid pref")
            op.pref = body["pref"]
        if "active" in body:
            op.active = bool(body["active"])
        crud.audit(session, "operator_updated", actor=actor or "dashboard",
                   via="dashboard", entity="operator", entity_id=operator_id, after=body)
        return _operator_out(op)


@router.delete("/operators/{operator_id}")
async def delete_operator(operator_id: int, actor: str = ""):
    with db_session() as session:
        op = session.get(TelegramOperator, operator_id)
        if op is None:
            raise HTTPException(404)
        session.delete(op)
        crud.audit(session, "operator_unlinked", actor=actor or "dashboard",
                   via="dashboard", entity="operator", entity_id=operator_id)
    return {"ok": True}


# ---------------------------------------------------------------------------
# audit log
# ---------------------------------------------------------------------------

@router.get("/audit_log")
async def list_audit(action: str = "", page: int = 1,
                     per_page: int = Query(default=50, le=200)):
    with db_session() as session:
        stmt = select(AuditLog)
        if action:
            stmt = stmt.where(AuditLog.action.like(f"%{action}%"))
        total = session.scalar(select(func.count()).select_from(stmt.subquery()))
        rows = session.scalars(stmt.order_by(desc(AuditLog.id))
                               .offset((page - 1) * per_page).limit(per_page))
        return {"data": [
            {"id": a.id, "actor": a.actor, "via": a.via, "action": a.action,
             "entity": a.entity, "entity_id": a.entity_id, "before": a.before,
             "after": a.after, "created_at": _iso(a.created_at)} for a in rows
        ], "total": total}
