"""C안: Gemma4 ∥ Haiku 병렬 비교 + Sonnet/Opus 승급.

설계: docs/reports/2026-04-17-session-archive-L2-summary-품질비교기준.md §6-bis
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable

from .local_client import LocalCallResult, call_local, ping
from .prompt import PromptContext, build_prompt
from .scorer import (
    COMPOSITE_GATE,
    ComparisonResult,
    Scores,
    decide_winner,
    evaluate,
    needs_opus_escalation,
)
from .summarize_client import (
    BudgetExceeded,
    MODEL_DEFAULT,
    MODEL_OPUS,
    MODEL_RETRY,
    ModelCall,
    _compute_cost,
    _parse_json_response,
    call_model,
    check_opus_quota,
    get_client,
)
from .trigger import Candidate


LOCAL_MODEL_LABEL = "gemma-4-26b-a4b-it"


@dataclass
class CompareStats:
    candidates: int = 0
    gemma_won: int = 0
    haiku_won: int = 0
    sonnet_won: int = 0
    opus_won: int = 0
    both_failed: int = 0
    opus_quota_blocks: int = 0
    budget_skips: int = 0
    total_cost_usd: float = 0.0


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _fetch_real_paths(conn: sqlite3.Connection, session_id: str) -> set[str]:
    rows = conn.execute(
        "SELECT file_paths_json FROM file_snapshots WHERE session_id = ? AND file_paths_json IS NOT NULL",
        (session_id,),
    ).fetchall()
    out: set[str] = set()
    for r in rows:
        try:
            for p in json.loads(r["file_paths_json"]):
                if isinstance(p, str):
                    out.add(p)
        except (TypeError, json.JSONDecodeError):
            continue
    return out


def _build_corpus(ctx: PromptContext) -> str:
    return ctx.user  # 프롬프트 user 블록 자체가 검사 대상 (meta + event 시퀀스)


def _call_gemma(ctx: PromptContext) -> tuple[ModelCall, LocalCallResult]:
    """로컬 Gemma4 호출 → ModelCall 규격으로 정규화."""
    raw = call_local(ctx.system, ctx.user)
    parsed = _parse_json_response(raw.text) if raw.text else None
    err = raw.error or (None if parsed is not None else "json_parse_failed")
    mc = ModelCall(
        model=raw.model,
        input_tokens=raw.input_tokens,
        output_tokens=raw.output_tokens,
        cost_usd=0.0,  # 로컬은 무료
        parsed=parsed,
        raw_text=raw.text,
        error=err,
        latency_ms=raw.latency_ms,
    )
    return mc, raw


def _upsert_candidate(
    conn: sqlite3.Connection,
    session_id: str,
    mc: ModelCall,
    scores: Scores,
) -> None:
    conn.execute(
        """
        INSERT INTO summary_candidates (
            session_id, model, parsed_json,
            schema_score, richness_score, grounding_score, self_quality, composite,
            chosen, input_tokens, output_tokens, cost_usd, latency_ms, error, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(session_id, model) DO UPDATE SET
            parsed_json = excluded.parsed_json,
            schema_score = excluded.schema_score,
            richness_score = excluded.richness_score,
            grounding_score = excluded.grounding_score,
            self_quality = excluded.self_quality,
            composite = excluded.composite,
            chosen = 0,
            input_tokens = excluded.input_tokens,
            output_tokens = excluded.output_tokens,
            cost_usd = excluded.cost_usd,
            latency_ms = excluded.latency_ms,
            error = excluded.error,
            created_at = excluded.created_at
        """,
        (
            session_id, mc.model,
            json.dumps(mc.parsed, ensure_ascii=False) if mc.parsed else None,
            scores.schema, scores.richness, scores.grounding, scores.self_quality, scores.composite,
            mc.input_tokens, mc.output_tokens, mc.cost_usd, mc.latency_ms, mc.error, _now_iso(),
        ),
    )


def _mark_chosen(conn: sqlite3.Connection, session_id: str, model: str) -> None:
    conn.execute(
        "UPDATE summary_candidates SET chosen = 0 WHERE session_id = ?",
        (session_id,),
    )
    conn.execute(
        "UPDATE summary_candidates SET chosen = 1 WHERE session_id = ? AND model = ?",
        (session_id, model),
    )


def _promote_to_summary(
    conn: sqlite3.Connection, cand: Candidate, mc: ModelCall, paths_from_snap: list[str]
) -> None:
    obj = mc.parsed or {}
    try:
        quality = int(obj.get("quality_score") or 0)
    except (TypeError, ValueError):
        quality = 0
    conn.execute(
        """
        INSERT INTO session_summaries (
            session_id, intent, outcome, decisions_json, tags_json,
            related_commits_json, files_touched_json,
            model, input_tokens, output_tokens, summary_cost_usd,
            summarized_at, quality_score
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(session_id) DO UPDATE SET
            intent = excluded.intent,
            outcome = excluded.outcome,
            decisions_json = excluded.decisions_json,
            tags_json = excluded.tags_json,
            related_commits_json = excluded.related_commits_json,
            files_touched_json = excluded.files_touched_json,
            model = excluded.model,
            input_tokens = excluded.input_tokens,
            output_tokens = excluded.output_tokens,
            summary_cost_usd = excluded.summary_cost_usd,
            summarized_at = excluded.summarized_at,
            quality_score = excluded.quality_score
        """,
        (
            cand.session_id,
            str(obj.get("intent") or "(missing)"),
            obj.get("outcome"),
            json.dumps(obj.get("decisions") or [], ensure_ascii=False),
            json.dumps(obj.get("tags") or [], ensure_ascii=False),
            json.dumps(cand.related_commits, ensure_ascii=False),
            json.dumps(obj.get("files_touched") or paths_from_snap, ensure_ascii=False),
            mc.model, mc.input_tokens, mc.output_tokens, mc.cost_usd,
            _now_iso(), quality,
        ),
    )


def _record_error(conn: sqlite3.Connection, sid: str, model: str | None, kind: str, err: str) -> None:
    conn.execute(
        "INSERT INTO summarize_errors (session_id, model, error_kind, error, seen_at) VALUES (?, ?, ?, ?, ?)",
        (sid, model, kind, err, _now_iso()),
    )


def _escalate(
    conn: sqlite3.Connection,
    client,
    cand: Candidate,
    ctx: PromptContext,
    corpus: str,
    real_paths: set[str],
    *,
    force_opus: bool,
) -> tuple[ModelCall | None, Scores | None, str]:
    """Sonnet → Opus 승급 체인. 반환: (최종 mc, scores, reason)."""
    if force_opus:
        ok, reason = check_opus_quota(conn)
        if not ok:
            _record_error(conn, cand.session_id, MODEL_OPUS, "opus_quota_exhausted", reason)
            return None, None, reason
        try:
            opus_mc = call_model(
                client, conn, model=MODEL_OPUS,
                system=ctx.system, user=ctx.user, est_input_tokens=ctx.est_input_tokens,
            )
        except BudgetExceeded as e:
            _record_error(conn, cand.session_id, MODEL_OPUS, "budget_exceeded", str(e))
            return None, None, "budget_exceeded"
        opus_scores = evaluate(opus_mc.parsed, corpus=corpus, real_paths=real_paths)
        _upsert_candidate(conn, cand.session_id, opus_mc, opus_scores)
        return opus_mc, opus_scores, "forced_opus"

    # Sonnet
    try:
        sonnet_mc = call_model(
            client, conn, model=MODEL_RETRY,
            system=ctx.system, user=ctx.user, est_input_tokens=ctx.est_input_tokens,
        )
    except BudgetExceeded as e:
        _record_error(conn, cand.session_id, MODEL_RETRY, "budget_exceeded", str(e))
        return None, None, "budget_exceeded"
    sonnet_scores = evaluate(sonnet_mc.parsed, corpus=corpus, real_paths=real_paths)
    _upsert_candidate(conn, cand.session_id, sonnet_mc, sonnet_scores)

    needs, reason = needs_opus_escalation(sonnet_scores)
    if not needs:
        return sonnet_mc, sonnet_scores, "sonnet_ok"

    ok, quota_reason = check_opus_quota(conn)
    if not ok:
        _record_error(conn, cand.session_id, MODEL_OPUS, "opus_quota_exhausted", quota_reason)
        return sonnet_mc, sonnet_scores, f"opus_blocked:{quota_reason}"

    try:
        opus_mc = call_model(
            client, conn, model=MODEL_OPUS,
            system=ctx.system, user=ctx.user, est_input_tokens=ctx.est_input_tokens,
        )
    except BudgetExceeded as e:
        _record_error(conn, cand.session_id, MODEL_OPUS, "budget_exceeded", str(e))
        return sonnet_mc, sonnet_scores, f"opus_blocked:budget"
    opus_scores = evaluate(opus_mc.parsed, corpus=corpus, real_paths=real_paths)
    _upsert_candidate(conn, cand.session_id, opus_mc, opus_scores)

    # Opus가 schema 통과하면 Opus, 아니면 Sonnet 결과 유지
    if opus_scores.schema >= COMPOSITE_GATE and opus_scores.composite > sonnet_scores.composite:
        return opus_mc, opus_scores, f"opus_{reason}"
    return sonnet_mc, sonnet_scores, f"opus_failed_{reason}"


def compare_one(
    conn: sqlite3.Connection,
    client,
    cand: Candidate,
    *,
    force_opus: bool = False,
) -> dict:
    """단일 세션 compare 실행. 반환: 요약 dict (on_progress 콜백에 전달)."""
    ctx = build_prompt(conn, cand.session_id, cand.related_commits)
    corpus = _build_corpus(ctx)
    real_paths = _fetch_real_paths(conn, cand.session_id)

    # 1) Gemma4 호출
    gemma_mc, _raw = _call_gemma(ctx)
    gemma_scores = evaluate(gemma_mc.parsed, corpus=corpus, real_paths=real_paths)
    _upsert_candidate(conn, cand.session_id, gemma_mc, gemma_scores)

    # 2) Haiku 호출
    try:
        haiku_mc = call_model(
            client, conn, model=MODEL_DEFAULT,
            system=ctx.system, user=ctx.user, est_input_tokens=ctx.est_input_tokens,
        )
        haiku_scores = evaluate(haiku_mc.parsed, corpus=corpus, real_paths=real_paths)
        _upsert_candidate(conn, cand.session_id, haiku_mc, haiku_scores)
    except BudgetExceeded as e:
        _record_error(conn, cand.session_id, MODEL_DEFAULT, "budget_exceeded", str(e))
        haiku_mc = None
        haiku_scores = None

    # 3) 승자 판정
    final_mc: ModelCall | None = None
    final_scores: Scores | None = None
    reason: str

    if force_opus:
        final_mc, final_scores, reason = _escalate(
            conn, client, cand, ctx, corpus, real_paths, force_opus=True
        )
        if final_mc is None:
            reason = f"forced_opus_failed:{reason}"
    elif haiku_mc is None:
        # Haiku 실패 시 Gemma 단독 판정
        if gemma_scores.schema >= COMPOSITE_GATE:
            final_mc, final_scores, reason = gemma_mc, gemma_scores, "gemma_only_haiku_budget"
        else:
            final_mc, final_scores, reason = _escalate(
                conn, client, cand, ctx, corpus, real_paths, force_opus=False
            )
    else:
        cmp = decide_winner(
            gemma_mc.model, gemma_scores, haiku_mc.model, haiku_scores,
            tie_preferred=gemma_mc.model,
        )
        reason = cmp.reason
        if cmp.winner == gemma_mc.model:
            final_mc, final_scores = gemma_mc, gemma_scores
        elif cmp.winner == haiku_mc.model:
            final_mc, final_scores = haiku_mc, haiku_scores
        else:
            # both_failed → Sonnet/Opus
            final_mc, final_scores, esc_reason = _escalate(
                conn, client, cand, ctx, corpus, real_paths, force_opus=False
            )
            reason = f"{cmp.reason}|{esc_reason}"

    # 4) chosen 마킹 + session_summaries promote
    snap_paths = list(real_paths)[:30]
    if final_mc is not None and final_scores is not None and final_scores.schema >= COMPOSITE_GATE:
        _mark_chosen(conn, cand.session_id, final_mc.model)
        _promote_to_summary(conn, cand, final_mc, snap_paths)
    else:
        _record_error(
            conn, cand.session_id, final_mc.model if final_mc else None,
            "all_failed", f"reason={reason}",
        )

    return {
        "session_id": cand.session_id,
        "winner_model": final_mc.model if final_mc else None,
        "winner_composite": final_scores.composite if final_scores else 0,
        "reason": reason,
        "gemma": (gemma_mc, gemma_scores),
        "haiku": (haiku_mc, haiku_scores) if haiku_mc else None,
    }


def compare_candidates(
    conn: sqlite3.Connection,
    candidates: Iterable[Candidate],
    *,
    force_opus: bool = False,
    on_progress=None,
    require_local: bool = True,
) -> CompareStats:
    stats = CompareStats()
    if require_local and not ping():
        raise RuntimeError(
            "LM Studio not reachable. Start LM Studio + load gemma-4-26b-a4b-it, "
            "or set LM_STUDIO_URL."
        )
    client = get_client()

    for cand in candidates:
        stats.candidates += 1
        try:
            result = compare_one(conn, client, cand, force_opus=force_opus)
        except BudgetExceeded:
            stats.budget_skips += 1
            break
        except Exception as e:
            _record_error(conn, cand.session_id, None, "unhandled", f"{type(e).__name__}: {e}")
            if on_progress:
                on_progress(cand, {"error": str(e)})
            continue

        # 통계 집계
        winner = result.get("winner_model")
        if winner == LOCAL_MODEL_LABEL or (winner and winner.startswith("gemma")):
            stats.gemma_won += 1
        elif winner == MODEL_DEFAULT:
            stats.haiku_won += 1
        elif winner == MODEL_RETRY:
            stats.sonnet_won += 1
        elif winner == MODEL_OPUS:
            stats.opus_won += 1
        else:
            stats.both_failed += 1

        # 비용 합산
        gm, _ = result["gemma"]
        stats.total_cost_usd += gm.cost_usd
        if result.get("haiku"):
            hm, _ = result["haiku"]
            stats.total_cost_usd += hm.cost_usd

        if on_progress:
            on_progress(cand, result)

    return stats
