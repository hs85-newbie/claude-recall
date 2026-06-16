"""ingest 입력 경로 override(--root / SESSION_ARCHIVE_ROOT) 이식성 테스트."""
from __future__ import annotations

import importlib
from pathlib import Path

from session_archive.ingest import scan_session_files


def test_scan_지정된_root의_jsonl을_찾아야_한다(tmp_path: Path) -> None:
    # Arrange
    (tmp_path / "proj-a").mkdir()
    (tmp_path / "proj-a" / "s1.jsonl").write_text("{}\n")
    (tmp_path / "proj-b").mkdir()
    (tmp_path / "proj-b" / "s2.jsonl").write_text("{}\n")
    (tmp_path / "proj-b" / "ignore.txt").write_text("x")

    # Act
    found = list(scan_session_files(tmp_path))

    # Assert
    assert [p.name for p in found] == ["s1.jsonl", "s2.jsonl"]


def test_scan_존재하지_않는_root는_빈_결과여야_한다(tmp_path: Path) -> None:
    # Arrange
    missing = tmp_path / "does-not-exist"

    # Act / Assert — 예외 없이 빈 이터레이터
    assert list(scan_session_files(missing)) == []


def test_env_SESSION_ARCHIVE_ROOT가_기본_경로를_override해야_한다(monkeypatch, tmp_path: Path) -> None:
    # Arrange
    monkeypatch.setenv("SESSION_ARCHIVE_ROOT", str(tmp_path / "custom"))

    # Act — import 시점 평가이므로 reload
    import session_archive.ingest as ingest_mod

    importlib.reload(ingest_mod)

    # Assert
    assert ingest_mod.CLAUDE_PROJECTS_ROOT == tmp_path / "custom"

    # Cleanup — 다른 테스트 오염 방지(env 제거 후 재로드는 monkeypatch가 처리)
    monkeypatch.delenv("SESSION_ARCHIVE_ROOT")
    importlib.reload(ingest_mod)


def test_env_미설정시_기본은_홈_claude_projects여야_한다(monkeypatch) -> None:
    # Arrange
    monkeypatch.delenv("SESSION_ARCHIVE_ROOT", raising=False)

    # Act
    import session_archive.ingest as ingest_mod

    importlib.reload(ingest_mod)

    # Assert
    assert ingest_mod.CLAUDE_PROJECTS_ROOT == Path.home() / ".claude" / "projects"
