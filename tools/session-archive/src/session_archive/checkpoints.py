"""gstack /context-save 체크포인트 증분 적재.

스캔: ~/.gstack/projects/<slug>/checkpoints/*.md  (env SESSION_ARCHIVE_CHECKPOINTS_ROOT로 override)
세션 요약과 별개 네임스페이스 — "다음 할 일/인계" 정보의 단기 RAG 소스.
체크포인트는 이미 간결한 마크다운이라 summarize 불필요: read → mask → upsert(FTS).
"""
from __future__ import annotations

import os
import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator

from .machine import machine_id
from .mask import mask_text

# WHY: 타 시스템 이식 — 체크포인트 위치가 다른 머신은 env로 override
CHECKPOINTS_ROOT = Path(
    os.environ.get("SESSION_ARCHIVE_CHECKPOINTS_ROOT", str(Path.home() / ".gstack" / "projects"))
).expanduser()

# 파일명 선두 타임스탬프: 20260507-125318-제목....md
_TS_RE = re.compile(r"^(\d{8})-(\d{6})-(.*)$")


# WHY: 머신 식별 로직은 machine.py로 추출(exporter와 공유). 기존 호출부 호환 유지.
_machine = machine_id


@dataclass
class CheckpointStats:
    files_scanned: int = 0
    upserted: int = 0
    skipped_unchanged: int = 0
    mask_hits: int = 0
    errors: list[str] = field(default_factory=list)


def scan_checkpoint_files(root: Path = CHECKPOINTS_ROOT) -> Iterator[Path]:
    if not root.exists():
        return
    yield from sorted(root.glob("*/checkpoints/*.md"))


def _parse_meta(path: Path) -> tuple[str, str, str]:
    """(created_at_iso, title, project_slug) 추출. 파일명·경로 기반."""
    slug = path.parent.parent.name  # <slug>/checkpoints/<file>.md
    stem = path.stem
    m = _TS_RE.match(stem)
    if m:
        d, t, title = m.group(1), m.group(2), m.group(3)
        created = f"{d[:4]}-{d[4:6]}-{d[6:8]}T{t[:2]}:{t[2:4]}:{t[4:6]}Z"
    else:
        created, title = "", stem
    return created, title.strip("-") or stem, slug


def ingest_checkpoints(
    conn: sqlite3.Connection,
    root: Path = CHECKPOINTS_ROOT,
    *,
    force: bool = False,
) -> CheckpointStats:
    stats = CheckpointStats()
    machine = _machine()
    for path in scan_checkpoint_files(root):
        stats.files_scanned += 1
        try:
            mtime = path.stat().st_mtime
            created, title, slug = _parse_meta(path)
            cid = f"{machine}::{slug}::{path.name}"

            if not force:
                row = conn.execute(
                    "SELECT source_mtime FROM checkpoints WHERE checkpoint_id = ?", (cid,)
                ).fetchone()
                if row is not None and abs(float(row["source_mtime"]) - mtime) < 1e-6:
                    stats.skipped_unchanged += 1
                    continue

            raw = path.read_text(encoding="utf-8", errors="replace")
            mr = mask_text(raw)
            stats.mask_hits += sum(mr.hits.values())

            conn.execute(
                """
                INSERT INTO checkpoints (
                    checkpoint_id, machine, project_slug, title, content,
                    created_at, source_file, source_mtime
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(checkpoint_id) DO UPDATE SET
                    machine = excluded.machine,
                    project_slug = excluded.project_slug,
                    title = excluded.title,
                    content = excluded.content,
                    created_at = excluded.created_at,
                    source_file = excluded.source_file,
                    source_mtime = excluded.source_mtime
                """,
                (cid, machine, slug, title, mr.text, created, str(path), mtime),
            )
            conn.execute("DELETE FROM checkpoints_fts WHERE checkpoint_id = ?", (cid,))
            conn.execute(
                "INSERT INTO checkpoints_fts (checkpoint_id, content) VALUES (?, ?)",
                (cid, mr.text),
            )
            stats.upserted += 1
        except Exception as e:  # noqa: BLE001 — 파일 1개 실패가 전체를 막지 않게
            stats.errors.append(f"{path.name}: {type(e).__name__}: {e}")
    return stats
