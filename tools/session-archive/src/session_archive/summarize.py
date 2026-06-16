"""L2 요약 오케스트레이션.

흐름: trigger.candidates → prompt.build → client.call_model → upsert session_summaries.
재시도 정책: Haiku 1회 + Sonnet 1회 (세션당 총 2회 상한).
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable

from .prompt import PromptContext, build_prompt
from .summarize_client import (
    BudgetExceeded,
    MODEL_DEFAULT,
    MODEL_RETRY,
    ModelCall,
    call_model,
    get_client,
    should_retry,
)
from .trigger import Candidate, iter_candidates


@dataclass
class SummarizeStats:
    candidates: int = 0
    succeeded: int = 0
    failed: int = 0
    retried_with_sonnet: int = 0
    skipped_budget: int = 0
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_cost_usd: float = 0.0


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _record_error(
    conn: sqlite3.Connection, session_id: str, model: str | None, kind: str, err: str
) -> None:
    conn.execute(
        """
        INSERT INTO summarize_errors (session_id, model, error_kind, error, seen_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (session_id, model, kind, err, _now_iso()),
    )


def _upsert_summary(
    conn: sqlite3.Connection,
    cand: Candidate,
    result: ModelCall,
    paths_from_snapshot: list[str],
) -> None:
    obj = result.parsed or {}
    intent = str(obj.get("intent") or "(missing)")
    outcome = obj.get("outcome")
    decisions = obj.get("decisions") or []
    tags = obj.get("tags") or []
    files_touched = obj.get("files_touched") or paths_from_snapshot
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
            intent,
            outcome,
            json.dumps(decisions, ensure_ascii=False),
            json.dumps(tags, ensure_ascii=False),
            json.dumps(cand.related_commits, ensure_ascii=False),
            json.dumps(files_touched, ensure_ascii=False),
            result.model,
            result.input_tokens,
            result.output_tokens,
            result.cost_usd,
            _now_iso(),
            quality,
        ),
    )


def _extract_paths(ctx: PromptContext) -> list[str]:
    """프롬프트 meta 블록에서 files_touched JSON을 재추출 (참조용 fallback)."""
    marker = "files_touched: "
    for line in ctx.user.splitlines():
        if line.startswith(marker):
            try:
                # 뒤의 " (+N more)" 제거
                payload = line[len(marker) :].split(" (+")[0]
                arr = json.loads(payload)
                return [p for p in arr if isinstance(p, str)]
            except (ValueError, json.JSONDecodeError):
                return []
    return []


def summarize_one(
    conn: sqlite3.Connection,
    client,
    cand: Candidate,
    *,
    allow_retry: bool = True,
) -> tuple[ModelCall | None, bool]:
    """세션 1개 요약. 반환: (최종 결과, sonnet_retried)."""
    ctx = build_prompt(conn, cand.session_id, cand.related_commits)

    try:
        result = call_model(
            client,
            conn,
            model=MODEL_DEFAULT,
            system=ctx.system,
            user=ctx.user,
            est_input_tokens=ctx.est_input_tokens,
        )
    except BudgetExceeded as e:
        _record_error(conn, cand.session_id, MODEL_DEFAULT, "budget_exceeded", str(e))
        return None, False

    retried = False
    if allow_retry and should_retry(result):
        if result.error:
            _record_error(
                conn, cand.session_id, MODEL_DEFAULT, "haiku_failed", result.error
            )
        try:
            retry_result = call_model(
                client,
                conn,
                model=MODEL_RETRY,
                system=ctx.system,
                user=ctx.user,
                est_input_tokens=ctx.est_input_tokens,
            )
            retried = True
            if retry_result.parsed is not None:
                result = retry_result
            elif retry_result.error:
                _record_error(
                    conn,
                    cand.session_id,
                    MODEL_RETRY,
                    "sonnet_failed",
                    retry_result.error,
                )
        except BudgetExceeded as e:
            _record_error(conn, cand.session_id, MODEL_RETRY, "budget_exceeded", str(e))

    if result.parsed is None:
        _record_error(
            conn,
            cand.session_id,
            result.model,
            "no_valid_json",
            result.error or "unknown",
        )
        return result, retried

    paths = _extract_paths(ctx)
    _upsert_summary(conn, cand, result, paths)
    return result, retried


def summarize_candidates(
    conn: sqlite3.Connection,
    candidates: Iterable[Candidate],
    *,
    stats: SummarizeStats | None = None,
    on_progress=None,
) -> SummarizeStats:
    stats = stats or SummarizeStats()
    client = get_client()

    for cand in candidates:
        stats.candidates += 1
        try:
            result, retried = summarize_one(conn, client, cand)
        except BudgetExceeded:
            stats.skipped_budget += 1
            break
        except Exception as e:
            _record_error(conn, cand.session_id, None, "unhandled", f"{type(e).__name__}: {e}")
            stats.failed += 1
            if on_progress:
                on_progress(cand, None)
            continue

        if retried:
            stats.retried_with_sonnet += 1
        if result and result.parsed is not None:
            stats.succeeded += 1
            stats.total_input_tokens += result.input_tokens
            stats.total_output_tokens += result.output_tokens
            stats.total_cost_usd += result.cost_usd
        else:
            stats.failed += 1

        if on_progress:
            on_progress(cand, result)

    return stats


def reeval_low_quality(
    conn: sqlite3.Connection,
    *,
    threshold: int = 5,
    limit: int | None = None,
) -> SummarizeStats:
    """quality_score < threshold인 요약을 Sonnet로 재생성."""
    sql = """
        SELECT ss.session_id, s.project_dir, s.started_at, s.ended_at, s.user_turn_count,
               ss.related_commits_json
        FROM session_summaries ss
        JOIN sessions s ON s.session_id = ss.session_id
        WHERE ss.quality_score < ?
        ORDER BY ss.quality_score ASC, ss.summarized_at ASC
    """
    rows = conn.execute(sql, (threshold,)).fetchall()
    if limit:
        rows = rows[:limit]

    cands: list[Candidate] = []
    for r in rows:
        try:
            commits = json.loads(r["related_commits_json"] or "[]")
        except json.JSONDecodeError:
            commits = []
        cands.append(
            Candidate(
                session_id=r["session_id"],
                project_dir=r["project_dir"],
                started_at=r["started_at"],
                ended_at=r["ended_at"],
                user_turns=r["user_turn_count"],
                related_commits=commits,
                reason="reeval",
            )
        )

    # reeval은 처음부터 Sonnet만 사용 (Haiku 이미 한 번 실패한 건이므로)
    stats = SummarizeStats()
    client = get_client()
    for cand in cands:
        stats.candidates += 1
        ctx = build_prompt(conn, cand.session_id, cand.related_commits)
        try:
            result = call_model(
                client,
                conn,
                model=MODEL_RETRY,
                system=ctx.system,
                user=ctx.user,
                est_input_tokens=ctx.est_input_tokens,
            )
        except BudgetExceeded as e:
            _record_error(conn, cand.session_id, MODEL_RETRY, "budget_exceeded", str(e))
            stats.skipped_budget += 1
            break

        if result.parsed is None:
            _record_error(
                conn, cand.session_id, MODEL_RETRY, "reeval_failed", result.error or "unknown"
            )
            stats.failed += 1
            continue
        paths = _extract_paths(ctx)
        _upsert_summary(conn, cand, result, paths)
        stats.succeeded += 1
        stats.total_input_tokens += result.input_tokens
        stats.total_output_tokens += result.output_tokens
        stats.total_cost_usd += result.cost_usd

    return stats
