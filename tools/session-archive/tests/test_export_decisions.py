"""exporter/decisions 단위 + 통합 테스트."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

from session_archive.exporter.decisions import (
    _short_hash,
    _slug,
    build_decision_body,
    build_decision_frontmatter,
    decision_filename,
    export_decisions,
)


# --- _slug ---

@pytest.mark.parametrize(
    "src,expected",
    [
        ("OCR 정책 결정", "OCR-정책-결정"),
        ("3-Leg 모델 채택, Cash Cow 폐기", "3-Leg-모델-채택-Cash-Cow-폐기"),
        ("api/v1!@# special", "apiv1-special"),
        ("", "untitled"),
        ("---", "untitled"),
        ("a" * 50, "a" * 30),
    ],
)
def test_slug(src: str, expected: str) -> None:
    assert _slug(src) == expected


# --- _short_hash ---

def test_short_hash_deterministic() -> None:
    assert _short_hash("sid-abc", 0) == _short_hash("sid-abc", 0)


def test_short_hash_different_index() -> None:
    assert _short_hash("sid-abc", 0) != _short_hash("sid-abc", 1)


def test_short_hash_length() -> None:
    assert len(_short_hash("sid", 0)) == 8


# --- decision_filename ---

def test_decision_filename_format() -> None:
    name = decision_filename("sid-1", "2026-04-25T14:30:00Z", "OCR 유지", 0)
    assert name.startswith("decisions/D-20260425-OCR-유지__")
    assert name.endswith(".md")


def test_decision_filename_unknown_date() -> None:
    name = decision_filename("sid", "", "결정", 0)
    assert "D-unknown-" in name


# --- build_decision_frontmatter ---

def test_frontmatter_9_fields() -> None:
    fm = build_decision_frontmatter(
        "sid", 0, "한국어 결정 내용", "근거 설명",
        "proj-a", "2026-04-25T00:00:00Z", '["t1", "T2"]', 8,
    )
    assert set(fm.keys()) == {
        "session_id", "machine", "decision_index", "project", "summarized_at",
        "kind", "lang", "tags", "quality_score",
    }


def test_frontmatter_includes_machine(monkeypatch) -> None:
    monkeypatch.setenv("SESSION_ARCHIVE_MACHINE", "test-laptop")
    fm = build_decision_frontmatter("s", 0, "결정", "", None, "2026-01-01", None, None)
    assert fm["machine"] == "test-laptop"


def test_frontmatter_kind_fixed() -> None:
    fm = build_decision_frontmatter("s", 0, "결정", "", None, "2026-01-01", None, None)
    assert fm["kind"] == "decision"


def test_frontmatter_lang_korean() -> None:
    fm = build_decision_frontmatter(
        "s", 0, "한국어 결정 자연성 검증", "한국어 근거 설명",
        None, "2026-01-01", None, None,
    )
    assert fm["lang"] == "ko"


# --- build_decision_body ---

def test_body_contains_required_sections() -> None:
    body = build_decision_body(
        "결정 내용", "근거 내용", "sessions/2026-01-01-p__abc12345",
        "p", "2026-01-01T00:00:00Z", ["t1", "t2"],
    )
    assert body.startswith("# 결정 내용")
    for h in ["## 결정", "## 근거", "## 컨텍스트", "## Tags"]:
        assert h in body
    assert "[[sessions/2026-01-01-p__abc12345]]" in body
    assert "[[projects/p]]" in body
    assert "#t1 #t2" in body


def test_body_no_rationale_placeholder() -> None:
    body = build_decision_body("결정", "", "sessions/x", None, "ts", [])
    assert "_없음_" in body
    assert "_미상_" in body


# --- export_decisions 통합 ---

@pytest.fixture
def db_with_decisions() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE sessions (
            session_id TEXT PRIMARY KEY, project_slug TEXT, git_branch TEXT,
            started_at TEXT, ended_at TEXT
        );
        CREATE TABLE session_summaries (
            session_id TEXT PRIMARY KEY, intent TEXT, outcome TEXT,
            decisions_json TEXT, tags_json TEXT, related_commits_json TEXT,
            files_touched_json TEXT, model TEXT, summarized_at TEXT NOT NULL,
            quality_score INTEGER
        );
        """
    )
    conn.executemany(
        "INSERT INTO sessions VALUES (?,?,?,?,?)",
        [
            ("sid-A", "proj-a", "main", "2026-04-25T00:00:00Z", "2026-04-25T01:00:00Z"),
            ("sid-B", "proj-b", "dev", "2026-04-26T00:00:00Z", None),
            ("sid-C", "proj-c", "main", "2026-04-27T00:00:00Z", None),
        ],
    )
    conn.executemany(
        "INSERT INTO session_summaries VALUES (?,?,?,?,?,?,?,?,?,?)",
        [
            ("sid-A", "intent A", "out A",
             json.dumps([
                 {"decision": "한국어 결정 1", "rationale": "한국어 근거"},
                 {"decision": "한국어 결정 2", "rationale": ""},
             ]),
             '["tag-a"]', "[]", "[]", "haiku", "2026-04-25T02:00:00Z", 8),
            ("sid-B", "intent B", "out B",
             json.dumps([{"decision": "Decision in English with rationale",
                          "rationale": "English reasoning here"}]),
             '["tag-b"]', "[]", "[]", "haiku", "2026-04-26T02:00:00Z", 7),
            ("sid-C", "intent C", "out C", "[]",  # decisions 비어있음
             "[]", "[]", "[]", "haiku", "2026-04-27T02:00:00Z", 6),
        ],
    )
    conn.commit()
    return conn


def test_export_decisions_writes_files(db_with_decisions, tmp_path: Path) -> None:
    stats = export_decisions(db_with_decisions, tmp_path)
    # candidates = decisions_json 비어있지 않은 행 = 2 (sid-A, sid-B)
    assert stats.candidates == 2
    # decisions_total = sid-A 2개 + sid-B 1개 = 3
    assert stats.decisions_total == 3
    assert stats.written == 3
    files = list((tmp_path / "decisions").glob("*.md"))
    assert len(files) == 3


def test_export_decisions_skip_empty_decision_text(
    db_with_decisions, tmp_path: Path
) -> None:
    db_with_decisions.execute(
        "INSERT INTO sessions VALUES ('sid-D','p','main','2026-04-28T00:00:00Z',NULL)"
    )
    db_with_decisions.execute(
        "INSERT INTO session_summaries VALUES ('sid-D',NULL,NULL,?,'[]','[]','[]','haiku','2026-04-28T01:00:00Z',5)",
        (json.dumps([{"decision": "  ", "rationale": "ignored"}]),),
    )
    db_with_decisions.commit()
    stats = export_decisions(db_with_decisions, tmp_path)
    assert stats.skipped == 1


def test_export_decisions_lang_distribution(
    db_with_decisions, tmp_path: Path
) -> None:
    stats = export_decisions(db_with_decisions, tmp_path)
    assert stats.by_lang["ko"] == 2
    assert stats.by_lang["en"] == 1


def test_export_decisions_filename_idempotent(
    db_with_decisions, tmp_path: Path
) -> None:
    """같은 session+index → 같은 파일명 (멱등)."""
    export_decisions(db_with_decisions, tmp_path)
    files1 = sorted(p.name for p in (tmp_path / "decisions").glob("*.md"))
    export_decisions(db_with_decisions, tmp_path)
    files2 = sorted(p.name for p in (tmp_path / "decisions").glob("*.md"))
    assert files1 == files2


def test_export_decisions_since_filter(db_with_decisions, tmp_path: Path) -> None:
    stats = export_decisions(db_with_decisions, tmp_path, since="2026-04-25T02:00:00Z")
    # sid-A (2026-04-25T02:00:00Z 자체는 since > 비교라 제외) → sid-B만
    assert stats.candidates == 1
    assert stats.decisions_total == 1
