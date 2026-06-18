"""gstack learnings.jsonl → vault 마크다운 export (오답노트 / 교훈 크로스머신 회상).

gstack가 프로젝트별로 쌓는 학습 기록(`~/.gstack/projects/<slug>/learnings.jsonl`)을
마스킹 마크다운으로 `vault/learnings/<machine>/<slug>.md` 에 멱등 생성한다.

WHY: 새 SQLite 테이블을 두지 않는다. learnings는 작은 코퍼스라 FTS 색인보다
grep+파일 직독이 단순하다(기록된 학습 `llm-recall-grep-over-fts`). 회상은 기존
`recall.search_vault`(grep)가 담당하고, 이 모듈은 vault로의 export만 책임진다.
단일 작성자 불변식(ADR-002 D3): `<machine>` 네임스페이스는 이 머신만 덮어쓴다.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator

from .machine import machine_id
from .mask import mask_text

# gstack 학습 저장소 루트. 체크포인트와 동일 디렉터리 계열.
LEARNINGS_ROOT = Path(
    os.environ.get(
        "SESSION_ARCHIVE_LEARNINGS_ROOT", str(Path.home() / ".gstack" / "projects")
    )
)


@dataclass
class LearningsStats:
    files_scanned: int = 0
    projects_written: int = 0
    learnings_total: int = 0
    mask_hits: int = 0
    errors: list[str] = field(default_factory=list)


def scan_learnings_files(root: Path = LEARNINGS_ROOT) -> Iterator[Path]:
    """`<root>/<slug>/learnings.jsonl` 파일들을 정렬 순서로 순회."""
    if not root.is_dir():
        return
    yield from sorted(root.glob("*/learnings.jsonl"))


def _parse_jsonl(path: Path) -> list[dict]:
    """JSONL 파싱 — 빈 줄/깨진 줄/비-dict는 건너뜀(상위 전파 안 함)."""
    out: list[dict] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            out.append(obj)
    return out


def render_markdown(slug: str, machine: str, learnings: list[dict]) -> str:
    """프로젝트 learnings → 마크다운. front-matter `machine`은 search_vault 머신 라벨용.

    마스킹은 호출부(export_learnings)에서 전체 문서에 일괄 적용한다.
    """
    head = [
        "---",
        f'machine: "{machine}"',
        f'project: "{slug}"',
        "kind: learnings",
        f"count: {len(learnings)}",
        "---",
        "",
        f"# 오답노트 / 교훈 — {slug}",
        "",
    ]
    body: list[str] = []
    # 최신 우선 (ts 내림차순; ts 없으면 빈 문자열로 뒤로).
    for item in sorted(learnings, key=lambda x: x.get("ts") or "", reverse=True):
        key = item.get("key") or "(no-key)"
        typ = item.get("type") or "?"
        conf = item.get("confidence")
        src = item.get("source") or "?"
        ts = item.get("ts") or ""
        skill = item.get("skill") or ""
        insight = item.get("insight") or ""
        files = item.get("files") or []

        title = f"## {key} · {typ}"
        if conf is not None:
            title += f" · confidence {conf}"
        body.append(title)

        meta = f"- source: {src}"
        if skill:
            meta += f" · skill: {skill}"
        if ts:
            meta += f" · {ts}"
        body.append(meta)
        if files:
            body.append(f"- files: {', '.join(str(f) for f in files)}")
        body.append("")
        body.append(insight)
        body.append("")
    return "\n".join(head + body).rstrip() + "\n"


def export_learnings(
    vault: Path,
    *,
    root: Path = LEARNINGS_ROOT,
    machine: str | None = None,
) -> LearningsStats:
    """learnings.jsonl → `vault/learnings/<machine>/<slug>.md` 멱등 재생성(마스킹)."""
    stats = LearningsStats()
    m = machine or machine_id()
    out_dir = vault / "learnings" / m
    for path in scan_learnings_files(root):
        stats.files_scanned += 1
        slug = path.parent.name
        try:
            learnings = _parse_jsonl(path)
        except OSError as e:
            stats.errors.append(f"{slug}: {e}")
            continue
        if not learnings:
            continue
        md = render_markdown(slug, m, learnings)
        masked = mask_text(md)
        stats.mask_hits += sum(masked.hits.values())
        out_dir.mkdir(parents=True, exist_ok=True)
        fname = re.sub(r"[^A-Za-z0-9_.-]", "-", slug) + ".md"
        (out_dir / fname).write_text(masked.text, encoding="utf-8")
        stats.projects_written += 1
        stats.learnings_total += len(learnings)
    return stats
