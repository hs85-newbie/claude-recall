"""exporter/__init__ (export_all) 통합 테스트."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from session_archive.exporter import export_all
from session_archive.exporter.state import read_watermark


@pytest.fixture
def populated_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE sessions (
            session_id     TEXT PRIMARY KEY,
            project_slug   TEXT,
            git_branch     TEXT,
            started_at     TEXT,
            ended_at       TEXT,
            promoted_to_l2 INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE session_summaries (
            session_id           TEXT PRIMARY KEY,
            intent               TEXT,
            outcome              TEXT,
            decisions_json       TEXT,
            tags_json            TEXT,
            related_commits_json TEXT,
            files_touched_json   TEXT,
            model                TEXT,
            summarized_at        TEXT NOT NULL,
            quality_score        INTEGER
        );
        CREATE TABLE events (
            uuid        TEXT,
            session_id  TEXT,
            role        TEXT,
            content     TEXT,
            timestamp   TEXT,
            token_count INTEGER,
            PRIMARY KEY (uuid, session_id)
        );
        """
    )
    conn.executemany(
        "INSERT INTO sessions (session_id, project_slug, git_branch, started_at, ended_at) VALUES (?,?,?,?,?)",
        [
            ("sid12345", "proj-a", "main", "2026-04-25T00:00:00Z", "2026-04-25T01:00:00Z"),
            ("sid67890", "proj-b", "dev", "2026-04-26T00:00:00Z", "2026-04-26T01:00:00Z"),
        ],
    )
    conn.executemany(
        "INSERT INTO session_summaries VALUES (?,?,?,?,?,?,?,?,?,?)",
        [
            ("sid12345", "한국어 정책 결정 자연성 검증",
             "한국어 결과 OCR 유지", "[]", '["t1"]', "[]", "[]",
             "claude-haiku-4-5-20251001", "2026-04-25T02:00:00Z", 8),
            ("sid67890", "intent B English", "outcome B", "[]", "[]", "[]", "[]",
             "claude-haiku-4-5-20251001", "2026-04-26T02:00:00Z", 7),
        ],
    )
    conn.executemany(
        "INSERT INTO events VALUES (?,?,?,?,?,?)",
        [
            ("u1", "sid12345", "user", "사용자 질문 A 첫 번째", "2026-04-25T00:30:00Z", 100),
            ("u2", "sid12345", "user", "사용자 질문 A 두 번째", "2026-04-25T00:31:00Z", 200),
            ("u3", "sid12345", "user", "사용자 질문 A 세 번째", "2026-04-25T00:32:00Z", 50),
            ("u4", "sid12345", "user", "네 번째 (제외 대상)", "2026-04-25T00:33:00Z", 30),
        ],
    )
    conn.commit()
    return conn


def test_export_all_writes_files(populated_db: sqlite3.Connection, tmp_path: Path) -> None:
    stats = export_all(populated_db, tmp_path)
    assert stats.candidates == 2
    assert stats.written == 2
    assert stats.failed == 0
    files = list((tmp_path / "sessions").glob("*.md"))
    assert len(files) == 2


def test_export_all_dry_run_no_files(populated_db: sqlite3.Connection, tmp_path: Path) -> None:
    stats = export_all(populated_db, tmp_path, dry_run=True)
    assert stats.skipped == 2
    assert stats.written == 0
    assert not (tmp_path / "sessions").exists()


def test_export_all_updates_watermark(populated_db: sqlite3.Connection, tmp_path: Path) -> None:
    export_all(populated_db, tmp_path)
    assert read_watermark(tmp_path) == "2026-04-26T02:00:00Z"


def test_export_all_dry_run_does_not_write_watermark(
    populated_db: sqlite3.Connection, tmp_path: Path
) -> None:
    export_all(populated_db, tmp_path, dry_run=True)
    assert read_watermark(tmp_path) is None


def test_export_all_incremental_since(
    populated_db: sqlite3.Connection, tmp_path: Path
) -> None:
    stats = export_all(populated_db, tmp_path, since="2026-04-25T02:00:00Z")
    # 2026-04-25 ts와 같은 행은 제외, 26만 처리
    assert stats.candidates == 1
    assert stats.written == 1


def test_export_all_full_overrides_watermark(
    populated_db: sqlite3.Connection, tmp_path: Path
) -> None:
    # 워터마크를 가장 최신 시점으로 미리 작성
    from session_archive.exporter.state import write_watermark

    write_watermark(tmp_path, "2099-01-01T00:00:00Z", 999)
    stats = export_all(populated_db, tmp_path, full=True)
    # full=True면 워터마크 무시 → 2건 모두 처리
    assert stats.candidates == 2
    assert stats.written == 2


def test_export_all_lang_distribution(
    populated_db: sqlite3.Connection, tmp_path: Path
) -> None:
    stats = export_all(populated_db, tmp_path)
    # 한국어 1, 영어 1
    assert stats.by_lang["ko"] == 1
    assert stats.by_lang["en"] == 1


def test_export_all_filters_image_base64(
    populated_db: sqlite3.Connection, tmp_path: Path
) -> None:
    """image multipart JSON은 user 원문에서 제외."""
    populated_db.execute(
        "INSERT INTO events VALUES (?,?,?,?,?,?)",
        ("u-img", "sid12345", "user",
         '{"type": "image", "source": {"data": "iVBORw0KG..."}}',
         "2026-04-25T00:34:00Z", 99999),
    )
    populated_db.commit()
    import hashlib
    export_all(populated_db, tmp_path)
    sid12_hash = hashlib.sha1(b"sid12345").hexdigest()[:8]
    sid12_file = next((tmp_path / "sessions").glob(f"*__{sid12_hash}.md"))
    text = sid12_file.read_text(encoding="utf-8")
    assert "iVBORw" not in text  # base64 prefix 미포함
    assert '"type": "image"' not in text


def test_export_all_user_quotes_top_3_by_token(
    populated_db: sqlite3.Connection, tmp_path: Path
) -> None:
    import hashlib
    export_all(populated_db, tmp_path)
    sid12_hash = hashlib.sha1(b"sid12345").hexdigest()[:8]
    sid12_file = next((tmp_path / "sessions").glob(f"*__{sid12_hash}.md"))
    text = sid12_file.read_text(encoding="utf-8")
    # token_count 200, 100, 50 → 상위 3개. 30은 제외.
    assert "두 번째" in text  # 200
    assert "첫 번째" in text  # 100
    assert "세 번째" in text  # 50
    assert "네 번째" not in text  # 30 (제외)


def test_export_all_empty_db(tmp_path: Path) -> None:
    conn = sqlite3.connect(":memory:")
    conn.executescript(
        """
        CREATE TABLE sessions (session_id TEXT PRIMARY KEY, project_slug TEXT,
            git_branch TEXT, started_at TEXT, ended_at TEXT);
        CREATE TABLE session_summaries (session_id TEXT PRIMARY KEY, intent TEXT,
            outcome TEXT, decisions_json TEXT, tags_json TEXT,
            related_commits_json TEXT, files_touched_json TEXT, model TEXT,
            summarized_at TEXT NOT NULL, quality_score INTEGER);
        CREATE TABLE events (uuid TEXT, session_id TEXT, role TEXT, content TEXT,
            timestamp TEXT, token_count INTEGER, PRIMARY KEY (uuid, session_id));
        """
    )
    stats = export_all(conn, tmp_path)
    assert stats.candidates == 0
    assert stats.written == 0
