"""L2 → Obsidian vault export 오케스트레이터 (E 단계).

설계: docs/E-export-obsidian-design.md
"""
from __future__ import annotations

import re
import sqlite3
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .body import build_body
from .decisions import DecisionStats, export_decisions
from .frontmatter import build_frontmatter, serialize
from .state import read_watermark, write_watermark
from .writer import write_session_md


DEFAULT_KINDS = ("sessions", "decisions")


@dataclass
class ExportStats:
    candidates: int = 0
    written: int = 0
    skipped: int = 0
    failed: int = 0
    by_lang: Counter = field(default_factory=Counter)
    last_summarized_at: str | None = None
    decisions: DecisionStats | None = None


_QUERY_SUMMARIES = """
SELECT
    ss.session_id            AS session_id,
    ss.intent                AS intent,
    ss.outcome               AS outcome,
    ss.decisions_json        AS decisions_json,
    ss.tags_json             AS tags_json,
    ss.related_commits_json  AS related_commits_json,
    ss.files_touched_json    AS files_touched_json,
    ss.model                 AS model,
    ss.summarized_at         AS summarized_at,
    ss.quality_score         AS quality_score,
    s.project_slug           AS project_slug,
    s.git_branch             AS git_branch,
    s.started_at             AS started_at,
    s.ended_at               AS ended_at
FROM session_summaries ss
JOIN sessions s USING (session_id)
WHERE ss.summarized_at > ?
ORDER BY ss.summarized_at ASC
"""

# WHY: 텍스트 후보를 충분히 가져와 비텍스트(image base64/multipart JSON) 필터링 후 상위 3개 선택
_QUERY_USER_QUOTES = """
SELECT content FROM events
WHERE session_id = ? AND role = 'user' AND content IS NOT NULL AND content != ''
ORDER BY (token_count IS NULL), token_count DESC, timestamp ASC
LIMIT 20
"""

USER_QUOTE_LIMIT = 3
_LARGE_LINE_NO_SPACE = 2000  # base64는 한 줄 길이 KB+에 공백 거의 없음
_MIN_CLEAN_LEN = 5

# WHY: user 메시지에 [Image #N] 마커 + image multipart JSON이 한 content에 섞임
_IMAGE_JSON = re.compile(r'\{"type":\s*"image"[^\n]*\}')
_BASE64_LIKE = re.compile(r'[A-Za-z0-9+/]{200,}={0,2}')


def _is_text_content(content: str | None) -> bool:
    """image base64·multipart JSON 같은 비텍스트 content 제외."""
    if not content:
        return False
    stripped = content.lstrip()
    if stripped.startswith(('{"type":', '[{', '{"image":')):
        return False
    first_line = stripped.split("\n", 1)[0]
    if len(first_line) > _LARGE_LINE_NO_SPACE and " " not in first_line[:200]:
        return False
    return True


def _clean_content(content: str) -> str:
    """image multipart JSON·긴 base64 토큰 제거 후 의미 있는 라인만 남김."""
    text = _IMAGE_JSON.sub("", content)
    text = _BASE64_LIKE.sub("[…base64 omitted]", text)
    return "\n".join(ln for ln in text.splitlines() if ln.strip())


def _fetch_user_quotes(conn: sqlite3.Connection, session_id: str) -> list[str]:
    rows = conn.execute(_QUERY_USER_QUOTES, (session_id,)).fetchall()
    out: list[str] = []
    for r in rows:
        c = r["content"]
        if not _is_text_content(c):
            continue
        cleaned = _clean_content(c)
        if len(cleaned) < _MIN_CLEAN_LEN:
            continue
        out.append(cleaned)
        if len(out) >= USER_QUOTE_LIMIT:
            break
    return out


def _resolve_since(vault_root: Path, since: str | None, full: bool) -> str:
    if full:
        return ""
    if since is not None:
        return since
    return read_watermark(vault_root) or ""


ProgressFn = Callable[[dict, "Path | None"], None]


def export_all(
    conn: sqlite3.Connection,
    vault_root: Path,
    *,
    since: str | None = None,
    full: bool = False,
    dry_run: bool = False,
    on_progress: ProgressFn | None = None,
    kinds: tuple[str, ...] = DEFAULT_KINDS,
) -> ExportStats:
    """L2 요약 → vault export. 증분 기준 = summarized_at.

    kinds: ('sessions',) v1 호환 / ('sessions', 'decisions') v1.5 기본 / 등.
    """
    conn.row_factory = sqlite3.Row
    threshold = _resolve_since(vault_root, since, full)

    stats = ExportStats()
    last_summarized: str | None = None

    if "sessions" in kinds:
        rows = conn.execute(_QUERY_SUMMARIES, (threshold,)).fetchall()
        stats.candidates = len(rows)
        for row in rows:
            try:
                fm_dict = build_frontmatter(row, row)
                stats.by_lang[fm_dict["lang"]] += 1
                last_summarized = row["summarized_at"]

                if dry_run:
                    stats.skipped += 1
                    if on_progress:
                        on_progress(dict(row), None)
                    continue

                quotes = _fetch_user_quotes(conn, row["session_id"])
                body_str = build_body(row, row, quotes)
                path = write_session_md(vault_root, row, serialize(fm_dict), body_str)
                # WHY: vault export 성공을 DB로 추적. trigger.py 후보 필터에서 사용
                conn.execute(
                    "UPDATE sessions SET promoted_to_l2 = 1 WHERE session_id = ?",
                    (row["session_id"],),
                )
                stats.written += 1
                if on_progress:
                    on_progress(dict(row), path)
            except (sqlite3.Error, OSError, ValueError):
                stats.failed += 1
                if on_progress:
                    on_progress(dict(row), None)

    if "decisions" in kinds and not dry_run:
        stats.decisions = export_decisions(conn, vault_root, since=threshold)

    if not dry_run and last_summarized:
        write_watermark(vault_root, last_summarized, exported_count=stats.written)

    stats.last_summarized_at = last_summarized
    return stats
