"""exporter/writer 단위 테스트."""
from __future__ import annotations

from pathlib import Path

import pytest

from session_archive.exporter.writer import (
    _format_date,
    session_filename,
    write_atomic,
    write_session_md,
)


# --- _format_date ---

@pytest.mark.parametrize(
    "ts,expected",
    [
        ("2026-04-25T14:30:00Z", "2026-04-25"),
        ("2026-04-25T14:30:00+00:00", "2026-04-25"),
        ("2026-04-25T23:30:00-09:00", "2026-04-26"),
        ("", "unknown"),
        ("not-a-date", "unknown"),
    ],
)
def test_format_date(ts: str, expected: str) -> None:
    assert _format_date(ts) == expected


# --- session_filename ---

def test_session_filename_uses_hash() -> None:
    """SHA-1 8자 사용 — 단순 prefix 아님."""
    import hashlib
    sid = "83351383-6381-42c3-b6b6-3cd393e1d043"
    expected_hash = hashlib.sha1(sid.encode()).hexdigest()[:8]
    row = {
        "session_id": sid,
        "project_slug": "-Users-cjons-tms-stt",
        "started_at": "2026-04-25T14:30:00Z",
    }
    assert (
        session_filename(row)
        == f"sessions/2026-04-25--Users-cjons-tms-stt__{expected_hash}.md"
    )


def test_session_filename_synthetic_parent_subagent_distinct() -> None:
    """합성키와 parent가 같은 hash가 아님 — 충돌 회피."""
    parent = "0a4fbd8d-77d0-46a3-9139-7cc377ebf6c2"
    subagent = f"{parent}::sub::agent-a54817e1969b7432a"
    common = {"project_slug": "p", "started_at": "2026-01-01T00:00:00Z"}
    f1 = session_filename({"session_id": parent, **common})
    f2 = session_filename({"session_id": subagent, **common})
    assert f1 != f2


def test_session_filename_unknown_date() -> None:
    row = {"session_id": "abcdefghxx", "project_slug": "p", "started_at": ""}
    out = session_filename(row)
    assert out.startswith("sessions/unknown-p__")
    assert out.endswith(".md")


def test_session_filename_none_session_id() -> None:
    row = {"session_id": None, "project_slug": "p", "started_at": "2026-01-01T00:00:00Z"}
    out = session_filename(row)
    assert "__unknown0" in out


# --- write_atomic ---

def test_write_atomic_creates_file(tmp_path: Path) -> None:
    target = tmp_path / "a" / "b.md"
    write_atomic(target, "hello")
    assert target.read_text(encoding="utf-8") == "hello"
    # tmp 잔여 없음
    assert not (tmp_path / "a" / "b.md.tmp").exists()


def test_write_atomic_overwrite(tmp_path: Path) -> None:
    target = tmp_path / "x.md"
    write_atomic(target, "v1")
    write_atomic(target, "v2")
    assert target.read_text(encoding="utf-8") == "v2"


def test_write_atomic_creates_parent_dirs(tmp_path: Path) -> None:
    target = tmp_path / "deep" / "nested" / "path" / "file.md"
    write_atomic(target, "content")
    assert target.exists()


# --- write_session_md ---

def test_write_session_md_combines(tmp_path: Path) -> None:
    import hashlib
    sid = "abcd1234-rest-of-uuid"
    expected_hash = hashlib.sha1(sid.encode()).hexdigest()[:8]
    row = {
        "session_id": sid,
        "project_slug": "proj",
        "started_at": "2026-04-25T00:00:00Z",
    }
    path = write_session_md(tmp_path, row, "---\nk: v\n---\n", "# title\n")
    assert path == tmp_path / "sessions" / f"2026-04-25-proj__{expected_hash}.md"
    text = path.read_text(encoding="utf-8")
    assert "---\nk: v\n---" in text
    assert "# title" in text


def test_write_session_md_idempotent(tmp_path: Path) -> None:
    row = {"session_id": "abcdefgh", "project_slug": "p", "started_at": "2026-01-01T00:00:00Z"}
    p1 = write_session_md(tmp_path, row, "---\n---\n", "v1")
    p2 = write_session_md(tmp_path, row, "---\n---\n", "v2")
    assert p1 == p2
    assert "v2" in p2.read_text(encoding="utf-8")
