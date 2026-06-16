"""SQLite 연결 및 스키마 초기화."""
from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

SCHEMA_VERSION = 3
DEFAULT_DB_PATH = Path.home() / ".claude-archive" / "sessions.db"
SCHEMA_SQL = Path(__file__).parent / "schema.sql"


def get_db_path() -> Path:
    """환경변수 SESSION_ARCHIVE_DB > 기본 경로."""
    override = os.environ.get("SESSION_ARCHIVE_DB")
    return Path(override) if override else DEFAULT_DB_PATH


def connect(db_path: Path | None = None) -> sqlite3.Connection:
    """DB 연결 + 스키마 초기화 (멱등)."""
    path = db_path or get_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA_SQL.read_text(encoding="utf-8"))
    _ensure_schema_version(conn)
    return conn


def _ensure_schema_version(conn: sqlite3.Connection) -> None:
    row = conn.execute(
        "SELECT version FROM schema_version ORDER BY version DESC LIMIT 1"
    ).fetchone()
    if row is None:
        conn.execute(
            "INSERT INTO schema_version (version, applied_at) VALUES (?, ?)",
            (SCHEMA_VERSION, datetime.now(timezone.utc).isoformat()),
        )
        return
    if row["version"] == SCHEMA_VERSION:
        return
    # WHY: 1→2는 L2 테이블 신규, 2→3는 summary_candidates + summarize_budget.opus_calls.
    # 새 테이블은 IF NOT EXISTS로 이미 처리됨. ALTER로 컬럼만 추가.
    current = row["version"]
    if current == 1:
        current = 2  # 1→2는 신규 테이블뿐
    if current == 2 and SCHEMA_VERSION >= 3:
        _migrate_add_opus_calls(conn)
        current = 3
    if current == SCHEMA_VERSION:
        conn.execute(
            "INSERT INTO schema_version (version, applied_at) VALUES (?, ?)",
            (SCHEMA_VERSION, datetime.now(timezone.utc).isoformat()),
        )
        return
    raise RuntimeError(
        f"schema_version mismatch: db={row['version']} code={SCHEMA_VERSION}"
    )


def _migrate_add_opus_calls(conn: sqlite3.Connection) -> None:
    """summarize_budget에 opus_calls 컬럼 추가 (멱등)."""
    cols = [r["name"] for r in conn.execute("PRAGMA table_info(summarize_budget)").fetchall()]
    if "opus_calls" not in cols:
        conn.execute("ALTER TABLE summarize_budget ADD COLUMN opus_calls INTEGER NOT NULL DEFAULT 0")
