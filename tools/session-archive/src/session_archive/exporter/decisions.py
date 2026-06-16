"""Decisions export (E v1.5).

설계: docs/E-export-obsidian-design.md §16
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from ..machine import machine_id
from .frontmatter import detect_lang, normalize_tags, serialize
from .writer import _format_date, write_atomic


DECISIONS_DIR = "decisions"
DECISION_SLUG_LEN = 30
DECISION_HASH_LEN = 8

_QUERY_DECISIONS = """
SELECT
    ss.session_id     AS session_id,
    ss.decisions_json AS decisions_json,
    ss.tags_json      AS tags_json,
    ss.summarized_at  AS summarized_at,
    ss.quality_score  AS quality_score,
    s.project_slug    AS project_slug,
    s.started_at      AS started_at
FROM session_summaries ss
JOIN sessions s USING (session_id)
WHERE ss.summarized_at > ?
  AND ss.decisions_json IS NOT NULL
  AND ss.decisions_json != '[]'
ORDER BY ss.summarized_at ASC
"""

_SLUG_UNSAFE = re.compile(r"[^a-zA-Z0-9가-힣\-]")


@dataclass
class DecisionStats:
    candidates: int = 0
    decisions_total: int = 0
    written: int = 0
    skipped: int = 0
    failed: int = 0
    by_lang: Counter = field(default_factory=Counter)


def _slug(text: str, max_len: int = DECISION_SLUG_LEN) -> str:
    """텍스트 → slug. 영숫자/한글/하이픈만, 공백→하이픈."""
    s = text.strip().replace(" ", "-")
    s = _SLUG_UNSAFE.sub("", s)
    if len(s) > max_len:
        s = s[:max_len]
    return s.strip("-") or "untitled"


def _short_hash(session_id: str, decision_index: int) -> str:
    """SHA-1(session_id + '#' + index) 앞 8자. 멱등 + 충돌 회피."""
    seed = f"{session_id}#{decision_index}"
    return hashlib.sha1(seed.encode()).hexdigest()[:DECISION_HASH_LEN]


def _format_yyyymmdd(iso_ts: str) -> str:
    date = _format_date(iso_ts)
    return date.replace("-", "") if date != "unknown" else "unknown"


def _session_link(session_id: str, project_slug: str | None, started_at: str | None) -> str:
    """원본 세션 파일에 대한 wikilink (writer.session_filename과 동일 규칙)."""
    sid8 = hashlib.sha1((session_id or "").encode()).hexdigest()[:8] if session_id else "unknown0"
    date = _format_date(started_at or "")
    slug = project_slug or "unknown"
    return f"sessions/{date}-{slug}__{sid8}"


def decision_filename(
    session_id: str,
    summarized_at: str,
    decision_text: str,
    decision_index: int,
) -> str:
    """decisions/D-YYYYMMDD-<slug>__<hash>.md"""
    return (
        f"{DECISIONS_DIR}/D-{_format_yyyymmdd(summarized_at)}-"
        f"{_slug(decision_text)}__{_short_hash(session_id, decision_index)}.md"
    )


def build_decision_frontmatter(
    session_id: str,
    decision_index: int,
    decision_text: str,
    rationale: str,
    project: str | None,
    summarized_at: str,
    tags_json: str | None,
    quality_score: int | None,
) -> dict:
    tags_raw = json.loads(tags_json or "[]")
    return {
        "session_id": session_id,
        # WHY: 크로스머신 회상 라벨 (frontmatter.build_frontmatter과 동일 규칙)
        "machine": machine_id(),
        "decision_index": decision_index,
        "project": project,
        "summarized_at": summarized_at,
        "kind": "decision",
        "lang": detect_lang(f"{decision_text} {rationale}"),
        "tags": normalize_tags(tags_raw),
        "quality_score": quality_score,
    }


def build_decision_body(
    decision_text: str,
    rationale: str,
    session_link_rel: str,
    project: str | None,
    summarized_at: str,
    tags: list[str],
) -> str:
    proj_line = f"- 프로젝트: [[projects/{project}]]" if project else "- 프로젝트: _미상_"
    tag_line = " ".join(f"#{t}" for t in tags) if tags else "_없음_"
    return "\n".join([
        f"# {decision_text}",
        "",
        "## 결정",
        "",
        decision_text,
        "",
        "## 근거",
        "",
        rationale or "_없음_",
        "",
        "## 컨텍스트",
        "",
        f"- 원본 세션: [[{session_link_rel}]]",
        proj_line,
        f"- 결정 시점: {summarized_at}",
        "",
        "## Tags",
        "",
        tag_line,
        "",
    ])


ProgressFn = Callable[[sqlite3.Row, int, "Path | None"], None]


def export_decisions(
    conn: sqlite3.Connection,
    vault_root: Path,
    *,
    since: str = "",
    on_progress: ProgressFn | None = None,
) -> DecisionStats:
    """session_summaries.decisions_json → vault/decisions/*.md."""
    conn.row_factory = sqlite3.Row
    rows = conn.execute(_QUERY_DECISIONS, (since,)).fetchall()

    stats = DecisionStats(candidates=len(rows))
    for row in rows:
        try:
            decisions = json.loads(row["decisions_json"] or "[]")
        except json.JSONDecodeError:
            stats.failed += 1
            continue
        session_link = _session_link(
            row["session_id"], row["project_slug"], row["started_at"]
        )
        for idx, d in enumerate(decisions):
            stats.decisions_total += 1
            decision_text = (d.get("decision") or "").strip() if isinstance(d, dict) else ""
            rationale = (d.get("rationale") or "").strip() if isinstance(d, dict) else ""
            if not decision_text:
                stats.skipped += 1
                continue
            try:
                fm = build_decision_frontmatter(
                    row["session_id"], idx, decision_text, rationale,
                    row["project_slug"], row["summarized_at"],
                    row["tags_json"], row["quality_score"],
                )
                stats.by_lang[fm["lang"]] += 1
                fname = decision_filename(
                    row["session_id"], row["summarized_at"], decision_text, idx,
                )
                path = vault_root / fname
                body_str = build_decision_body(
                    decision_text, rationale, session_link,
                    row["project_slug"], row["summarized_at"], fm["tags"],
                )
                write_atomic(path, serialize(fm) + "\n" + body_str)
                stats.written += 1
                if on_progress:
                    on_progress(row, idx, path)
            except (OSError, ValueError):
                stats.failed += 1
    return stats
