"""commit_match 단위 테스트."""
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from session_archive.commit_match import (
    _has_git_dir,
    _parse_iso,
    find_related_commits,
)


def test_parse_iso_with_z():
    dt = _parse_iso("2026-04-17T10:00:00Z")
    assert dt is not None
    assert dt.tzinfo is not None


def test_parse_iso_invalid():
    assert _parse_iso("not-a-date") is None
    assert _parse_iso("") is None


def test_has_git_dir_false_for_nonexistent():
    assert _has_git_dir("/no/such/path") is False


def test_has_git_dir_true(tmp_path):
    (tmp_path / ".git").mkdir()
    assert _has_git_dir(str(tmp_path)) is True


def test_find_related_commits_no_git(tmp_path):
    assert find_related_commits(str(tmp_path), "2026-04-17T00:00:00Z", None) == []


def test_find_related_commits_real_repo(tmp_path, monkeypatch):
    """임시 git 리포 만들어서 커밋 1개 매칭되는지 확인."""
    import subprocess

    def git(*args):
        subprocess.run(
            ["git", "-C", str(tmp_path), *args],
            check=True,
            capture_output=True,
            env={
                "GIT_AUTHOR_NAME": "t",
                "GIT_AUTHOR_EMAIL": "t@t",
                "GIT_COMMITTER_NAME": "t",
                "GIT_COMMITTER_EMAIL": "t@t",
                "HOME": str(tmp_path),
                "PATH": "/usr/bin:/bin:/usr/local/bin",
            },
        )

    git("init", "-q")
    (tmp_path / "a.txt").write_text("hi")
    git("add", ".")
    git("commit", "-q", "-m", "x")

    now = datetime.now(timezone.utc)
    started = (now - timedelta(minutes=5)).isoformat()
    ended = now.isoformat()
    commits = find_related_commits(str(tmp_path), started, ended)
    assert len(commits) == 1
    assert len(commits[0]) == 40  # full SHA
