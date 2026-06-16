"""L2 요약 대상 세션 필터.

조건 (OR):
1. user_turn_count >= 3 AND (now - ended_at) >= 6h  — 종료된 의미 있는 세션
2. 세션 기간 동안 project_dir에 git 커밋 발생
3. 수동 승격 — 호출자가 force 플래그로 전달

제외:
- 이미 session_summaries에 행 있음
- promoted_to_l2 = 1 (수동 승격된 사용자 확정건)
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from .commit_match import find_related_commits


MIN_USER_TURNS = 3
MIN_IDLE_AFTER_END = timedelta(hours=6)


@dataclass
class Candidate:
    session_id: str
    project_dir: str
    started_at: str
    ended_at: str | None
    user_turns: int
    related_commits: list[str]
    reason: str  # "idle" | "commits" | "forced"


def _parse_iso(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None


def _is_idle_enough(ended_at: str | None, now: datetime) -> bool:
    end_dt = _parse_iso(ended_at)
    if end_dt is None:
        return False
    if end_dt.tzinfo is None:
        end_dt = end_dt.replace(tzinfo=timezone.utc)
    return (now - end_dt) >= MIN_IDLE_AFTER_END


def iter_candidates(
    conn: sqlite3.Connection,
    *,
    force_session_id: str | None = None,
    limit: int | None = None,
    now: datetime | None = None,
) -> list[Candidate]:
    """L2 요약 대상 세션 목록 반환."""
    now = now or datetime.now(timezone.utc)

    if force_session_id:
        rows = conn.execute(
            """
            SELECT session_id, project_dir, started_at, ended_at, user_turn_count
            FROM sessions
            WHERE session_id = ?
            """,
            (force_session_id,),
        ).fetchall()
        return [
            Candidate(
                session_id=r["session_id"],
                project_dir=r["project_dir"],
                started_at=r["started_at"],
                ended_at=r["ended_at"],
                user_turns=r["user_turn_count"],
                related_commits=find_related_commits(
                    r["project_dir"], r["started_at"], r["ended_at"]
                ),
                reason="forced",
            )
            for r in rows
        ]

    sql = """
        SELECT s.session_id, s.project_dir, s.started_at, s.ended_at, s.user_turn_count
        FROM sessions s
        LEFT JOIN session_summaries ss ON ss.session_id = s.session_id
        WHERE ss.session_id IS NULL
          AND s.user_turn_count >= ?
        ORDER BY s.ended_at DESC
    """
    rows = conn.execute(sql, (MIN_USER_TURNS,)).fetchall()

    candidates: list[Candidate] = []
    for r in rows:
        idle = _is_idle_enough(r["ended_at"], now)
        commits = find_related_commits(r["project_dir"], r["started_at"], r["ended_at"])
        if not idle and not commits:
            continue
        reason = "commits" if commits else "idle"
        candidates.append(
            Candidate(
                session_id=r["session_id"],
                project_dir=r["project_dir"],
                started_at=r["started_at"],
                ended_at=r["ended_at"],
                user_turns=r["user_turn_count"],
                related_commits=commits,
                reason=reason,
            )
        )
        if limit and len(candidates) >= limit:
            break
    return candidates
