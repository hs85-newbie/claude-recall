"""vault(~/llm-wiki) git 운반 — 크로스머신 전송 계층.

pull(타 머신 데이터 수신) / commit+push(로컬 export 송신)을 캡슐화한다.
파이프라인(자동)과 `sync-vault` CLI(수동)가 공유. 모든 함수는 (ok, message) 반환 —
어떤 실패도 예외로 던지지 않아 04:00 파이프라인이 한 단계 실패로 죽지 않는다.
"""
from __future__ import annotations

import subprocess
from pathlib import Path


def is_vault_repo(vault_root: Path | str) -> bool:
    return (Path(vault_root).expanduser() / ".git").is_dir()


def _git(vault_root: Path, args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(vault_root), *args],
        capture_output=True, text=True,
    )


def _err(r: subprocess.CompletedProcess) -> str:
    return (r.stderr or r.stdout or "").strip()[:300]


def vault_pull(vault_root: Path | str) -> tuple[bool, str]:
    """타 머신이 push한 vault 변경을 수신. ff-only 우선, 발산 시 rebase."""
    root = Path(vault_root).expanduser()
    if not is_vault_repo(root):
        return False, f"vault is not a git repo: {root}"
    r = _git(root, ["pull", "--ff-only"])
    if r.returncode == 0:
        return True, r.stdout.strip() or "up to date"
    r2 = _git(root, ["pull", "--rebase"])
    if r2.returncode == 0:
        return True, "rebased: " + (r2.stdout.strip()[:200] or "ok")
    return False, _err(r2) or _err(r)


def vault_commit_push(vault_root: Path | str, message: str) -> tuple[bool, str]:
    """로컬 export 변경을 commit+push. push 거부(remote ahead) 시 rebase 후 재시도.

    변경 없으면 commit 생략하고 미푸시 커밋만 push 시도.
    """
    root = Path(vault_root).expanduser()
    if not is_vault_repo(root):
        return False, f"vault is not a git repo: {root}"
    _git(root, ["add", "-A"])
    status = _git(root, ["status", "--porcelain"])
    if status.stdout.strip():
        c = _git(root, ["commit", "-m", message])
        if c.returncode != 0:
            return False, "commit failed: " + _err(c)
    push = _git(root, ["push"])
    if push.returncode == 0:
        return True, "pushed"
    # remote ahead → rebase 후 재push
    pr = _git(root, ["pull", "--rebase"])
    if pr.returncode != 0:
        return False, "push rejected, rebase failed: " + _err(pr)
    push2 = _git(root, ["push"])
    if push2.returncode == 0:
        return True, "pushed after rebase"
    return False, "push failed after rebase: " + _err(push2)
