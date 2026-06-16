"""파일명 규칙 + atomic write.

설계: docs/E-export-obsidian-design.md §3, §11
"""
from __future__ import annotations

import hashlib
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SESSIONS_DIR = "sessions"
SID_HASH_LEN = 8


def _row_get(row: sqlite3.Row | dict, key: str) -> Any:
    try:
        return row[key]
    except (KeyError, IndexError):
        return None


def _format_date(iso_ts: str) -> str:
    """ISO timestamp → YYYY-MM-DD (UTC). 파싱 실패 시 'unknown'."""
    if not iso_ts:
        return "unknown"
    try:
        dt = datetime.fromisoformat(iso_ts.replace("Z", "+00:00"))
    except ValueError:
        return "unknown"
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%d")


def _short_id(session_id: str | None) -> str:
    """session_id → SHA-1 hash 앞 8자.

    WHY: 단순 prefix는 합성키 `{parent}::sub::{agent_stem}`이 parent와
    같은 prefix를 가져 충돌(74% 손실 실측). hash로 회피.
    """
    if not session_id:
        return "unknown0"
    return hashlib.sha1(session_id.encode("utf-8")).hexdigest()[:SID_HASH_LEN]


def session_filename(session_row: sqlite3.Row | dict) -> str:
    """sessions/YYYY-MM-DD-<projslug>__<sid8>.md (sid8 = SHA-1 8자)."""
    sid = _row_get(session_row, "session_id")
    sid8 = _short_id(sid)
    slug = _row_get(session_row, "project_slug") or "unknown"
    date = _format_date(_row_get(session_row, "started_at") or "")
    return f"{SESSIONS_DIR}/{date}-{slug}__{sid8}.md"


def write_atomic(path: Path, content: str) -> None:
    """tmp 작성 → fsync → rename. 같은 mount에서 atomic 보장."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(content)
        f.flush()
        os.fsync(f.fileno())
    tmp.replace(path)


def write_session_md(
    vault_root: Path,
    session_row: sqlite3.Row | dict,
    frontmatter_str: str,
    body_str: str,
) -> Path:
    """vault_root에 frontmatter + body 결합 → 파일 작성, 절대 경로 반환."""
    path = vault_root / session_filename(session_row)
    write_atomic(path, frontmatter_str + "\n" + body_str)
    return path
