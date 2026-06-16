"""trigger 단위 테스트."""
import sqlite3
from datetime import datetime, timedelta, timezone

from session_archive.db import SCHEMA_SQL
from session_archive.trigger import (
    MIN_IDLE_AFTER_END,
    _is_idle_enough,
    iter_candidates,
)


def _mk_db():
    conn = sqlite3.connect(":memory:", isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA_SQL.read_text(encoding="utf-8"))
    return conn


def _insert_session(conn, sid, *, started, ended, turns=5, project="/nogit"):
    conn.execute(
        """INSERT INTO sessions (
            session_id, project_dir, project_slug, started_at, ended_at,
            event_count, user_turn_count, assistant_turn_count, git_branch,
            source_file, source_mtime, source_last_uuid, promoted_to_l2
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)""",
        (sid, project, "slug", started, ended, turns * 2, turns, turns,
         None, f"/fake/{sid}.jsonl", 0.0, None),
    )


def test_is_idle_enough():
    now = datetime.now(timezone.utc)
    old = (now - MIN_IDLE_AFTER_END - timedelta(minutes=1)).isoformat()
    fresh = (now - timedelta(minutes=10)).isoformat()
    assert _is_idle_enough(old, now) is True
    assert _is_idle_enough(fresh, now) is False
    assert _is_idle_enough(None, now) is False


def test_iter_candidates_skips_fresh():
    conn = _mk_db()
    now = datetime.now(timezone.utc)
    # fresh: 10분 전 종료
    _insert_session(
        conn, "s_fresh",
        started=(now - timedelta(hours=1)).isoformat(),
        ended=(now - timedelta(minutes=10)).isoformat(),
    )
    # idle: 7시간 전 종료 → 트리거
    _insert_session(
        conn, "s_idle",
        started=(now - timedelta(hours=8)).isoformat(),
        ended=(now - timedelta(hours=7)).isoformat(),
    )
    cands = iter_candidates(conn, now=now)
    sids = {c.session_id for c in cands}
    assert "s_idle" in sids
    assert "s_fresh" not in sids


def test_iter_candidates_skips_already_summarized():
    conn = _mk_db()
    now = datetime.now(timezone.utc)
    _insert_session(
        conn, "s_done",
        started=(now - timedelta(hours=8)).isoformat(),
        ended=(now - timedelta(hours=7)).isoformat(),
    )
    conn.execute(
        """INSERT INTO session_summaries (
            session_id, intent, model, summarized_at, quality_score
        ) VALUES (?, ?, ?, ?, ?)""",
        ("s_done", "x", "m", now.isoformat(), 8),
    )
    cands = iter_candidates(conn, now=now)
    assert all(c.session_id != "s_done" for c in cands)


def test_iter_candidates_skips_low_turns():
    conn = _mk_db()
    now = datetime.now(timezone.utc)
    _insert_session(
        conn, "s_low",
        started=(now - timedelta(hours=8)).isoformat(),
        ended=(now - timedelta(hours=7)).isoformat(),
        turns=2,
    )
    cands = iter_candidates(conn, now=now)
    assert all(c.session_id != "s_low" for c in cands)


def test_force_session_bypasses_filters():
    conn = _mk_db()
    now = datetime.now(timezone.utc)
    _insert_session(
        conn, "s_low",
        started=(now - timedelta(minutes=10)).isoformat(),
        ended=(now - timedelta(minutes=5)).isoformat(),
        turns=1,
    )
    cands = iter_candidates(conn, force_session_id="s_low", now=now)
    assert len(cands) == 1
    assert cands[0].reason == "forced"
