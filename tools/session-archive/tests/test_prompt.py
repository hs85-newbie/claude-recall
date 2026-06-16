"""prompt 빌더 단위 테스트."""
import sqlite3

from session_archive.db import SCHEMA_SQL
from session_archive.prompt import (
    MAX_INPUT_TOKENS,
    _estimate_tokens,
    build_prompt,
)


def _mk_db():
    conn = sqlite3.connect(":memory:", isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA_SQL.read_text(encoding="utf-8"))
    return conn


def _seed_session(conn, sid="s1", *, n_user=3, n_assistant=3, project="/fake/proj"):
    conn.execute(
        """INSERT INTO sessions (
            session_id, project_dir, project_slug, started_at, ended_at,
            event_count, user_turn_count, assistant_turn_count, git_branch,
            source_file, source_mtime, source_last_uuid, promoted_to_l2
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)""",
        (sid, project, "fake-proj", "2026-04-17T00:00:00Z", "2026-04-17T01:00:00Z",
         n_user + n_assistant, n_user, n_assistant, "main",
         "/fake/s1.jsonl", 0.0, None),
    )
    rows = []
    for i in range(n_user):
        rows.append((sid, f"u{i}", None, "user", f"2026-04-17T00:{i:02d}:00Z",
                     "user", f"사용자 질문 {i}", "h", None, "/x", "main", 0, 10))
    for i in range(n_assistant):
        rows.append((sid, f"a{i}", None, "assistant", f"2026-04-17T00:{i:02d}:30Z",
                     "assistant", f"assistant 답변 {i}" + " x" * 500, "h",
                     "Read" if i == 0 else None, "/x", "main", 0, 10))
    conn.executemany(
        """INSERT INTO events (
            session_id, uuid, parent_uuid, type, timestamp, role, content,
            content_hash, tool_name, cwd, git_branch, masked, token_count
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        rows,
    )


def test_build_prompt_basic():
    conn = _mk_db()
    _seed_session(conn)
    ctx = build_prompt(conn, "s1", ["abc1234", "def5678"])
    assert "사용자 질문 0" in ctx.user
    assert "[ASSISTANT tool=Read]" in ctx.user
    assert "abc12345" in ctx.user or "abc1234" in ctx.user  # 최소 8자 prefix
    assert ctx.est_input_tokens > 0
    assert ctx.truncation_level == 0


def test_build_prompt_skips_tool_results():
    conn = _mk_db()
    _seed_session(conn, n_user=1, n_assistant=0)
    # tool_result 형태의 user 이벤트 추가
    conn.execute(
        """INSERT INTO events (
            session_id, uuid, parent_uuid, type, timestamp, role, content,
            content_hash, tool_name, cwd, git_branch, masked, token_count
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        ("s1", "tr1", None, "user", "2026-04-17T00:10:00Z",
         "user", "[tool_result] 거대한 파일 내용", "h", None, "/x", "main", 0, 10),
    )
    ctx = build_prompt(conn, "s1", [])
    assert "[tool_result]" not in ctx.user


def test_build_prompt_truncation_for_huge_session():
    conn = _mk_db()
    # 대용량 세션: user 500턴 (각 800자 = 합 40만자 ≒ 10만 토큰)
    _seed_session(conn, n_user=500, n_assistant=500)
    ctx = build_prompt(conn, "s1", [])
    assert ctx.est_input_tokens <= MAX_INPUT_TOKENS * 1.1  # 절단 후 허용 마진
    assert ctx.truncation_level >= 1


def test_build_prompt_missing_session_raises():
    conn = _mk_db()
    try:
        build_prompt(conn, "no-such", [])
    except ValueError:
        return
    assert False, "expected ValueError"


def test_estimate_tokens_monotone():
    assert _estimate_tokens("ab") < _estimate_tokens("a" * 100)


def test_build_prompt_caps_giant_user_turn():
    """단일 초대형 user 턴이 있어도 est_input_tokens는 한도 내 유지.

    회귀: idle 세션의 465K자 user 턴이 컨텍스트 윈도우(200K)를 넘겨
    400(prompt too long)을 유발하던 결함 방어.
    """
    conn = _mk_db()
    conn.execute(
        """INSERT INTO sessions (
            session_id, project_dir, project_slug, started_at, ended_at,
            event_count, user_turn_count, assistant_turn_count, git_branch,
            source_file, source_mtime, source_last_uuid, promoted_to_l2
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)""",
        ("big", "/p", "p", "2026-04-17T00:00:00Z", "2026-04-17T01:00:00Z",
         1, 1, 0, "main", "/big.jsonl", 0.0, None),
    )
    conn.execute(
        """INSERT INTO events (
            session_id, uuid, parent_uuid, type, timestamp, role, content,
            content_hash, tool_name, cwd, git_branch, masked, token_count
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        ("big", "u0", None, "user", "2026-04-17T00:00:00Z", "user",
         "X" * 800_000, "h", None, "/x", "main", 0, 200_000),
    )
    ctx = build_prompt(conn, "big", [])
    assert ctx.est_input_tokens <= MAX_INPUT_TOKENS
