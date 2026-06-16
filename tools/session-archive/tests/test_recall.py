"""recall.py 읽기 전용 질의 단위 테스트."""
import json
import sqlite3

import pytest

from session_archive import recall
from session_archive.db import SCHEMA_SQL


def _mk_db():
    conn = sqlite3.connect(":memory:", isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA_SQL.read_text(encoding="utf-8"))
    return conn


def _seed_session_event(conn, *, sid="s1", slug="proj-a", uuid="u1",
                        ts="2026-06-10T00:00:00Z", role="user", content="토큰 대시보드 구현"):
    conn.execute(
        "INSERT INTO sessions (session_id, project_dir, project_slug, started_at, source_file, source_mtime) "
        "VALUES (?, ?, ?, ?, ?, 0)",
        (sid, f"/x/{slug}", slug, ts, "f.jsonl"),
    )
    conn.execute(
        "INSERT INTO events (session_id, uuid, type, timestamp, role, content) VALUES (?, ?, 'message', ?, ?, ?)",
        (sid, uuid, ts, role, content),
    )
    conn.execute(
        "INSERT INTO events_fts (uuid, session_id, content) VALUES (?, ?, ?)",
        (uuid, sid, content),
    )


def _seed_checkpoint(conn, *, cid="m::proj-a::cp1.md", slug="proj-a", machine="m",
                     title="handoff", created_at="2026-06-11T00:00:00Z", content="다음 작업: STT Clova 연동"):
    conn.execute(
        "INSERT INTO checkpoints (checkpoint_id, machine, project_slug, title, content, created_at, source_file, source_mtime) "
        "VALUES (?, ?, ?, ?, ?, ?, 'f.md', 0)",
        (cid, machine, slug, title, content, created_at),
    )
    conn.execute(
        "INSERT INTO checkpoints_fts (checkpoint_id, content) VALUES (?, ?)",
        (cid, content),
    )


def _seed_summary(conn, *, sid="s1", decisions=None, summarized_at="2026-06-10T01:00:00Z", quality=8):
    decisions = decisions if decisions is not None else [{"decision": "STT는 Clova로 고정", "rationale": "정확도"}]
    conn.execute(
        "INSERT INTO session_summaries (session_id, intent, decisions_json, model, summarized_at, quality_score) "
        "VALUES (?, 'x', ?, 'haiku', ?, ?)",
        (sid, json.dumps(decisions, ensure_ascii=False), summarized_at, quality),
    )


# ── sanitize / parse_since ──

def test_sanitize_keeps_korean_and_ops():
    assert recall.sanitize_fts_query("토큰 AND 대시보드") == "토큰 AND 대시보드"


def test_sanitize_quotes_special_tokens():
    assert recall.sanitize_fts_query("a-b") == '"a-b"'


def test_parse_since_none():
    assert recall.parse_since(None) is None


def test_parse_since_invalid_raises():
    with pytest.raises(ValueError):
        recall.parse_since("5x")


# ── search ──

def test_search_events_matches_korean(tmp_path):
    conn = _mk_db()
    _seed_session_event(conn, content="토큰 대시보드 구현")
    rows = recall.search_events(conn, "토큰")
    assert len(rows) == 1
    assert rows[0]["project_slug"] == "proj-a"


def test_search_events_project_filter():
    conn = _mk_db()
    _seed_session_event(conn, sid="s1", slug="proj-a", uuid="u1", content="공통어 토큰")
    _seed_session_event(conn, sid="s2", slug="proj-b", uuid="u2", content="공통어 토큰")
    rows = recall.search_events(conn, "토큰", project="proj-a")
    assert {r["project_slug"] for r in rows} == {"proj-a"}


def test_search_history_combines_events_and_checkpoints():
    conn = _mk_db()
    _seed_session_event(conn, content="STT 토큰")
    _seed_checkpoint(conn, content="STT Clova 연동 토큰")
    out = recall.search_history(conn, "토큰")
    assert len(out["events"]) == 1
    assert len(out["checkpoints"]) == 1


# ── decisions ──

def test_recall_decisions_flattens():
    conn = _mk_db()
    _seed_session_event(conn)
    _seed_summary(conn, decisions=[
        {"decision": "STT는 Clova", "rationale": "정확도"},
        {"decision": "DB는 sqlite", "rationale": "단순성"},
    ])
    out = recall.recall_decisions(conn)
    assert len(out) == 2
    assert out[0]["decision"] == "STT는 Clova"
    assert out[0]["session_id"] == "s1"


def test_recall_decisions_query_filter():
    conn = _mk_db()
    _seed_session_event(conn)
    _seed_summary(conn, decisions=[
        {"decision": "STT는 Clova", "rationale": "정확도"},
        {"decision": "DB는 sqlite", "rationale": "단순성"},
    ])
    out = recall.recall_decisions(conn, query="sqlite")
    assert len(out) == 1
    assert "sqlite" in out[0]["decision"]


def test_recall_decisions_skips_empty_and_malformed():
    conn = _mk_db()
    _seed_session_event(conn, sid="s1")
    _seed_session_event(conn, sid="s2", uuid="u2")
    _seed_summary(conn, sid="s1", decisions=[{"decision": "", "rationale": "빈 결정"}])
    # malformed json
    conn.execute(
        "INSERT INTO session_summaries (session_id, intent, decisions_json, model, summarized_at) "
        "VALUES ('s2', 'x', '{bad', 'haiku', '2026-06-10T02:00:00Z')",
    )
    out = recall.recall_decisions(conn)
    assert out == []


def test_recall_decisions_limit():
    conn = _mk_db()
    _seed_session_event(conn)
    _seed_summary(conn, decisions=[{"decision": f"d{i}", "rationale": "r"} for i in range(10)])
    out = recall.recall_decisions(conn, limit=3)
    assert len(out) == 3


# ── checkpoints ──

def test_recent_checkpoints_orders_desc():
    conn = _mk_db()
    _seed_checkpoint(conn, cid="m::p::a.md", created_at="2026-06-01T00:00:00Z", title="old")
    _seed_checkpoint(conn, cid="m::p::b.md", created_at="2026-06-15T00:00:00Z", title="new")
    out = recall.recent_checkpoints(conn)
    assert out[0]["title"] == "new"
    assert out[1]["title"] == "old"


def test_recent_checkpoints_project_filter():
    conn = _mk_db()
    _seed_checkpoint(conn, cid="m::a::x.md", slug="proj-a")
    _seed_checkpoint(conn, cid="m::b::y.md", slug="proj-b")
    out = recall.recent_checkpoints(conn, project="proj-b")
    assert len(out) == 1
    assert out[0]["project_slug"] == "proj-b"


def test_connect_ro_missing_db_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        recall.connect_ro(tmp_path / "nope.db")


# ── search_vault (크로스머신 grep) ──

def _mk_vault(root):
    """tmp vault: sessions/decisions(front-matter machine) + checkpoints(<machine>/ 경로)."""
    (root / "sessions").mkdir(parents=True)
    (root / "decisions").mkdir(parents=True)
    (root / "checkpoints" / "machineA" / "proj-a").mkdir(parents=True)
    (root / "sessions" / "2026-01-01-proj__a1.md").write_text(
        "---\nmachine: machineA\nproject: proj-a\nkind: session\n---\n# 세션 요약\nSTT는 Clova로 고정했다.\n",
        encoding="utf-8",
    )
    (root / "decisions" / "D-20260101-stt__d1.md").write_text(
        "---\nmachine: machineB\nkind: decision\n---\n# STT 결정\n근거: 정확도\n",
        encoding="utf-8",
    )
    # front-matter에 machine 없는 구버전 파일 → unknown
    (root / "decisions" / "D-20251201-old__d2.md").write_text(
        "---\nkind: decision\n---\n# 옛 결정\n토큰 대시보드 구현\n",
        encoding="utf-8",
    )
    (root / "checkpoints" / "machineA" / "proj-a" / "cp1.md").write_text(
        "## NEXT\n남은 작업: 토큰 인덱스\n", encoding="utf-8",
    )
    return root


def test_search_vault_finds_session(tmp_path):
    _mk_vault(tmp_path)
    hits = recall.search_vault("Clova", vault_root=tmp_path)
    assert len(hits) == 1
    h = hits[0]
    assert h["kind"] == "sessions"
    assert h["machine"] == "machineA"
    assert "Clova" in h["snippet"]
    assert h["path"].startswith("sessions/")


def test_search_vault_machine_from_path_for_checkpoints(tmp_path):
    _mk_vault(tmp_path)
    hits = recall.search_vault("토큰", vault_root=tmp_path, kind="checkpoints")
    assert len(hits) == 1
    assert hits[0]["machine"] == "machineA"  # 경로에서 추출


def test_search_vault_machine_unknown_when_no_frontmatter(tmp_path):
    _mk_vault(tmp_path)
    hits = recall.search_vault("토큰 대시보드", vault_root=tmp_path, kind="decisions")
    assert len(hits) == 1
    assert hits[0]["machine"] == "unknown"


def test_search_vault_machine_filter(tmp_path):
    _mk_vault(tmp_path)
    hits = recall.search_vault("결정", vault_root=tmp_path, machine="machineB")
    assert all(h["machine"] == "machineB" for h in hits)
    assert len(hits) == 1


def test_search_vault_kind_filter(tmp_path):
    _mk_vault(tmp_path)
    hits = recall.search_vault("STT", vault_root=tmp_path, kind="sessions")
    assert all(h["kind"] == "sessions" for h in hits)


def test_search_vault_multitoken_and(tmp_path):
    _mk_vault(tmp_path)
    # 두 토큰 모두 포함하는 파일만
    assert recall.search_vault("STT Clova", vault_root=tmp_path)
    assert recall.search_vault("STT 없는단어", vault_root=tmp_path) == []


def test_search_vault_missing_vault_returns_empty(tmp_path):
    assert recall.search_vault("x", vault_root=tmp_path / "nope") == []


def test_search_vault_no_match_returns_empty(tmp_path):
    _mk_vault(tmp_path)
    assert recall.search_vault("존재하지않는토큰xyz", vault_root=tmp_path) == []


def test_search_vault_empty_query_returns_empty(tmp_path):
    _mk_vault(tmp_path)
    assert recall.search_vault("   ", vault_root=tmp_path) == []


def test_search_vault_limit(tmp_path):
    (tmp_path / "sessions").mkdir(parents=True)
    for i in range(5):
        (tmp_path / "sessions" / f"s{i}.md").write_text(
            f"---\nmachine: m\n---\n공통토큰 {i}\n", encoding="utf-8",
        )
    hits = recall.search_vault("공통토큰", vault_root=tmp_path, limit=3)
    assert len(hits) == 3
