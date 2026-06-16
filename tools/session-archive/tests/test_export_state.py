"""exporter/state 단위 테스트."""
from __future__ import annotations

from pathlib import Path

from session_archive.exporter.state import (
    read_watermark,
    state_path,
    write_watermark,
)


def test_state_path(tmp_path: Path) -> None:
    assert state_path(tmp_path) == tmp_path / ".session-archive-state.json"


def test_read_watermark_missing(tmp_path: Path) -> None:
    assert read_watermark(tmp_path) is None


def test_read_watermark_invalid_json(tmp_path: Path) -> None:
    state_path(tmp_path).write_text("not json", encoding="utf-8")
    assert read_watermark(tmp_path) is None


def test_read_watermark_non_dict(tmp_path: Path) -> None:
    state_path(tmp_path).write_text('"a string"', encoding="utf-8")
    assert read_watermark(tmp_path) is None


def test_read_watermark_missing_field(tmp_path: Path) -> None:
    state_path(tmp_path).write_text('{"other": "x"}', encoding="utf-8")
    assert read_watermark(tmp_path) is None


def test_round_trip(tmp_path: Path) -> None:
    write_watermark(tmp_path, "2026-04-25T14:30:00Z", exported_count=42)
    assert read_watermark(tmp_path) == "2026-04-25T14:30:00Z"


def test_write_watermark_overwrite(tmp_path: Path) -> None:
    write_watermark(tmp_path, "2026-04-01T00:00:00Z", 1)
    write_watermark(tmp_path, "2026-04-25T00:00:00Z", 5)
    assert read_watermark(tmp_path) == "2026-04-25T00:00:00Z"
