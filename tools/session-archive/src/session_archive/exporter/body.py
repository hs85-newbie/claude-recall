"""본문 빌더 (7섹션).

설계: docs/E-export-obsidian-design.md §5
"""
from __future__ import annotations

import json
import re
import sqlite3
from typing import Any


USER_QUOTE_TRUNC = 500
COMMIT_SHA_LEN = 7
DECISION_MAX = 5


def _row_get(row: sqlite3.Row | dict, key: str, default: Any = None) -> Any:
    try:
        v = row[key]
    except (KeyError, IndexError):
        return default
    return v if v is not None else default


def _section_intent_title(intent: str) -> str:
    """H1 — intent 첫 줄. 빈 값은 '(no intent)'."""
    src = (intent or "").strip()
    title = src.splitlines()[0] if src else "(no intent)"
    return f"# {title}"


def _section_intent(intent: str) -> str:
    body = (intent or "").strip() or "_없음_"
    return f"## Intent\n\n{body}"


def _section_outcome(outcome: str | None) -> str:
    body = (outcome or "").strip() or "_없음_"
    return f"## Outcome\n\n{body}"


def _section_decisions(decisions_json: str | None) -> str:
    decisions = json.loads(decisions_json or "[]")[:DECISION_MAX]
    if not decisions:
        return "## Decisions\n\n_없음_"
    lines = ["## Decisions", ""]
    for i, d in enumerate(decisions, 1):
        decision = (d.get("decision") or "").strip()
        rationale = (d.get("rationale") or "").strip()
        lines.append(f"{i}. **{decision}**")
        if rationale:
            lines.append(f"   - 근거: {rationale}")
    return "\n".join(lines)


def _section_files(files_json: str | None) -> str:
    files = json.loads(files_json or "[]")
    if not files:
        return "## Files Touched\n\n_없음_"
    lines = ["## Files Touched", ""]
    lines.extend(f"- {f}" for f in files)
    return "\n".join(lines)


def _section_commits(related_commits_json: str | None) -> str:
    """v1은 SHA 7자만. 메시지 60자 추가는 v2 (design notes §15)."""
    commits = json.loads(related_commits_json or "[]")
    if not commits:
        return "## Related Commits\n\n_없음_"
    lines = ["## Related Commits", ""]
    lines.extend(f"- {sha[:COMMIT_SHA_LEN]}" for sha in commits)
    return "\n".join(lines)


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "…"


def _section_user_quotes(quotes: list[str]) -> str:
    if not quotes:
        return "## User 원문\n\n_없음_"
    lines = ["## User 원문 (token 기준 상위 3턴)", ""]
    for i, q in enumerate(quotes, 1):
        truncated = _truncate(q.strip(), USER_QUOTE_TRUNC)
        lines.append(f"> [!quote] {i}")
        for ln in truncated.splitlines():
            lines.append(f"> {ln}")
        lines.append("")
    return "\n".join(lines).rstrip()


_WIKILINK_UNSAFE = re.compile(r"[^a-zA-Z0-9가-힣\-]")


def _wikilink_slug(s: str) -> str:
    """경로 → 슬래시→하이픈, 확장자 제거, 안전하지 않은 문자 → _."""
    last = s.rsplit("/", 1)[-1]
    base = s.rsplit(".", 1)[0] if "." in last else s
    return _WIKILINK_UNSAFE.sub("_", base.replace("/", "-"))


def _section_backlinks(project_slug: str | None, files_json: str | None) -> str:
    files = json.loads(files_json or "[]")
    lines = ["## Backlinks", ""]
    if project_slug:
        lines.append(f"- Project: [[projects/{project_slug}]]")
    for f in files:
        lines.append(f"- File: [[files/{_wikilink_slug(f)}]]")
    if len(lines) == 2:
        return "## Backlinks\n\n_없음_"
    return "\n".join(lines)


def build_body(
    session_row: sqlite3.Row | dict,
    summary_row: sqlite3.Row | dict,
    user_quotes: list[str],
) -> str:
    """7섹션 본문 빌드 (frontmatter 제외)."""
    intent = _row_get(summary_row, "intent", "")
    outcome = _row_get(summary_row, "outcome", "")
    sections = [
        _section_intent_title(intent),
        "",
        _section_intent(intent),
        "",
        _section_outcome(outcome),
        "",
        _section_decisions(_row_get(summary_row, "decisions_json")),
        "",
        _section_files(_row_get(summary_row, "files_touched_json")),
        "",
        _section_commits(_row_get(summary_row, "related_commits_json")),
        "",
        _section_user_quotes(user_quotes),
        "",
        _section_backlinks(
            _row_get(session_row, "project_slug"),
            _row_get(summary_row, "files_touched_json"),
        ),
        "",
    ]
    return "\n".join(sections)
