"""세션 기간에 project_dir에서 발생한 git 커밋 SHA 수집.

세션이 L2 요약 트리거가 되는 조건 중 "코드 산출물 있음" 판별에 사용.
"""
from __future__ import annotations

import subprocess
from datetime import datetime, timedelta
from pathlib import Path


# WHY: 세션 종료 직후 커밋/푸시가 흔하므로 윈도우를 ended_at + 2h로 확장
COMMIT_WINDOW_AFTER = timedelta(hours=2)


def _has_git_dir(project_dir: str) -> bool:
    try:
        p = Path(project_dir)
    except (ValueError, OSError):
        return False
    if not p.exists() or not p.is_dir():
        return False
    return (p / ".git").exists()


def _parse_iso(ts: str) -> datetime | None:
    try:
        # fromisoformat는 'Z' suffix를 3.11부터 지원
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


def find_related_commits(
    project_dir: str,
    started_at: str,
    ended_at: str | None,
    *,
    window_after: timedelta = COMMIT_WINDOW_AFTER,
    timeout_sec: float = 5.0,
) -> list[str]:
    """project_dir에서 [started_at, ended_at + window_after] 범위의 커밋 SHA 반환.

    - .git 없거나 git 실행 실패 시 빈 리스트 (에러로 간주 안 함)
    - --all로 모든 브랜치 포함
    """
    if not _has_git_dir(project_dir):
        return []

    start_dt = _parse_iso(started_at)
    end_dt = _parse_iso(ended_at) if ended_at else None
    if start_dt is None:
        return []
    if end_dt is None:
        end_dt = start_dt + timedelta(hours=1)
    until_dt = end_dt + window_after

    cmd = [
        "git",
        "-C",
        project_dir,
        "log",
        "--all",
        f"--since={start_dt.isoformat()}",
        f"--until={until_dt.isoformat()}",
        "--format=%H",
    ]
    try:
        out = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout_sec,
            check=False,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return []
    if out.returncode != 0:
        return []
    return [sha.strip() for sha in out.stdout.splitlines() if sha.strip()]
