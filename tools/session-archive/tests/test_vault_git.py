"""vault git 운반 통합 테스트 — tmp bare remote + 클론 2개로 실제 git 동작 검증.

push/push-reject(rebase 재시도)/pull/머신A→B E2E + 비-repo 안전 처리.
"""
import subprocess

import pytest

from session_archive import vault_git


def _git(d, *args):
    subprocess.run(["git", "-C", str(d), *args], check=True, capture_output=True, text=True)


def _clone(remote, dest, email):
    subprocess.run(["git", "clone", str(remote), str(dest)], check=True, capture_output=True, text=True)
    _git(dest, "config", "user.email", email)
    _git(dest, "config", "user.name", email.split("@")[0])


@pytest.fixture
def remote_and_clones(tmp_path):
    """bare remote + 클론 A/B, A가 seed 커밋을 push해 main 브랜치 생성."""
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", "-b", "main", str(remote)], check=True, capture_output=True)
    a = tmp_path / "A"
    _clone(remote, a, "a@x")
    (a / "decisions").mkdir()
    (a / "decisions" / "seed.md").write_text("seed\n", encoding="utf-8")
    _git(a, "add", "-A")
    _git(a, "commit", "-m", "seed")
    _git(a, "push", "origin", "main")
    b = tmp_path / "B"
    _clone(remote, b, "b@x")
    return remote, a, b


def test_is_vault_repo(tmp_path, remote_and_clones):
    _, a, _ = remote_and_clones
    assert vault_git.is_vault_repo(a)
    assert not vault_git.is_vault_repo(tmp_path / "nope")


def test_commit_push_then_pull_cross_machine(remote_and_clones):
    """머신 A가 결정 push → 머신 B가 pull → B에 A의 파일 도착 (크로스머신 핵심)."""
    _, a, b = remote_and_clones
    (a / "decisions" / "stt.md").write_text("---\nmachine: A\n---\nSTT는 Clova\n", encoding="utf-8")
    ok, msg = vault_git.vault_commit_push(a, "export A")
    assert ok, msg

    ok2, msg2 = vault_git.vault_pull(b)
    assert ok2, msg2
    assert (b / "decisions" / "stt.md").exists()
    assert "Clova" in (b / "decisions" / "stt.md").read_text(encoding="utf-8")


def test_push_reject_then_rebase_retry(remote_and_clones):
    """B가 먼저 push해 remote가 앞서면, A의 push는 reject→rebase 후 성공해야 한다."""
    _, a, b = remote_and_clones
    # B가 먼저 커밋 push (remote 전진)
    (b / "decisions" / "b1.md").write_text("from B\n", encoding="utf-8")
    ok_b, _ = vault_git.vault_commit_push(b, "B first")
    assert ok_b
    # A는 stale 상태에서 커밋 → push reject → rebase 재시도 경로
    (a / "decisions" / "a1.md").write_text("from A\n", encoding="utf-8")
    ok_a, msg = vault_git.vault_commit_push(a, "A second")
    assert ok_a, msg
    assert "rebase" in msg or msg == "pushed"
    # remote에 둘 다 반영 — B가 다시 pull하면 a1 도착
    vault_git.vault_pull(b)
    assert (b / "decisions" / "a1.md").exists()


def test_commit_push_noop_when_clean(remote_and_clones):
    """변경 없으면 commit 생략하고 push는 up-to-date로 성공."""
    _, a, _ = remote_and_clones
    ok, msg = vault_git.vault_commit_push(a, "nothing")
    assert ok, msg


def test_pull_non_repo_is_safe(tmp_path):
    ok, msg = vault_git.vault_pull(tmp_path / "not-a-repo")
    assert ok is False
    assert "not a git repo" in msg


def test_commit_push_non_repo_is_safe(tmp_path):
    ok, msg = vault_git.vault_commit_push(tmp_path / "not-a-repo", "x")
    assert ok is False
    assert "not a git repo" in msg
