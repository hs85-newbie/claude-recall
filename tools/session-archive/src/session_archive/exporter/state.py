"""Watermark JSON 파일 read/write.

설계: docs/E-export-obsidian-design.md §8
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .writer import write_atomic


STATE_FILENAME = ".session-archive-state.json"


def state_path(vault_root: Path) -> Path:
    return vault_root / STATE_FILENAME


def read_watermark(vault_root: Path) -> str | None:
    """last_summarized_at ISO 문자열 반환. 파일/필드 없으면 None."""
    path = state_path(vault_root)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    if not isinstance(data, dict):
        return None
    val = data.get("last_summarized_at")
    return val if isinstance(val, str) else None


def write_watermark(
    vault_root: Path,
    last_summarized_at: str,
    exported_count: int = 0,
) -> None:
    """워터마크 + export 카운트 갱신 (atomic)."""
    payload: dict[str, Any] = {
        "last_summarized_at": last_summarized_at,
        "exported_count": exported_count,
    }
    write_atomic(
        state_path(vault_root),
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
    )
