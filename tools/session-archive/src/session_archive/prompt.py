"""L2 요약용 프롬프트 빌더.

events → 토큰 예산 내 컨텍스트 문자열 변환.
설계 노트: docs/L2-design-notes.md

절단 정책 (순서대로):
  1. tool_result/subagent chrome 제외 (기본)
  2. assistant 본문 200자 → 100자
  3. 오래된 user 턴부터 제외 (최근 우선)
  4. 앞뒤 10턴만 샘플링 + 중간 생략 마커
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass


# 입력 토큰 상한. Haiku/Sonnet 기준 안전 마진.
MAX_INPUT_TOKENS = 20_000
ASSISTANT_PREVIEW_DEFAULT = 200
ASSISTANT_PREVIEW_TIGHT = 100
# WHY: user 턴도 캡 — 단일 초대형 user 턴(붙여넣은 로그/파일)이 컨텍스트를 독식해
#      200K 초과 400(prompt too long)을 유발하던 결함 방지
USER_PREVIEW = 2000
SAMPLE_WINDOW = 10


SYSTEM_PROMPT = """당신은 Claude Code 세션 로그를 요약하는 도구입니다.

반드시 다음 JSON 스키마만 출력하세요 (마크다운/설명/코드펜스 금지):
{
  "intent": "사용자가 하려고 한 것 (한 줄)",
  "outcome": "실제 결과 (커밋/결정/포기, 한두 줄)",
  "decisions": [{"decision": "...", "rationale": "..."}],
  "tags": ["프로젝트·기술·주제 키워드"],
  "files_touched": ["세션에서 편집/생성된 파일 경로"],
  "quality_score": 0
}

규칙:
- decisions: 최대 5개. 세션에서 실제로 내려진 판단만.
- tags: 소문자 영숫자·하이픈. 3~8개 권장.
- files_touched: file-history-snapshot 기록 기반. 없으면 빈 배열.
- quality_score: 이 요약에 대한 당신의 확신도 (0=모름, 10=명확).
- 한국어로 작성.
- JSON 이외 문자 금지."""


@dataclass
class PromptContext:
    session_id: str
    system: str
    user: str
    est_input_tokens: int
    truncation_level: int  # 0=원본, 1=assistant 축소, 2=오래된 user 제외, 3=샘플링


def _estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


def _fetch_events(conn: sqlite3.Connection, session_id: str) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT timestamp, type, role, tool_name, content
        FROM events
        WHERE session_id = ?
        ORDER BY timestamp
        """,
        (session_id,),
    ).fetchall()


def _fetch_snapshot_paths(conn: sqlite3.Connection, session_id: str) -> list[str]:
    rows = conn.execute(
        """
        SELECT file_paths_json FROM file_snapshots
        WHERE session_id = ? AND file_paths_json IS NOT NULL
        ORDER BY timestamp
        """,
        (session_id,),
    ).fetchall()
    paths: list[str] = []
    seen: set[str] = set()
    for r in rows:
        try:
            arr = json.loads(r["file_paths_json"])
        except (TypeError, json.JSONDecodeError):
            continue
        for p in arr:
            if isinstance(p, str) and p not in seen:
                seen.add(p)
                paths.append(p)
    return paths


def _fetch_session_meta(conn: sqlite3.Connection, session_id: str) -> sqlite3.Row | None:
    return conn.execute(
        """
        SELECT session_id, project_dir, project_slug, git_branch,
               started_at, ended_at,
               user_turn_count, assistant_turn_count, event_count
        FROM sessions WHERE session_id = ?
        """,
        (session_id,),
    ).fetchone()


def _format_turn(ev: sqlite3.Row, assistant_preview: int) -> str | None:
    """단일 이벤트 → 프롬프트 라인. 스킵 시 None."""
    content = ev["content"] or ""
    role = ev["role"]
    etype = ev["type"]
    tool = ev["tool_name"]

    # 절단 규칙 1: user 턴 중 tool_result는 스킵
    if etype == "user" and content.startswith("[tool_result]"):
        return None

    if etype == "user":
        preview = content[:USER_PREVIEW]
        if len(content) > USER_PREVIEW:
            preview += "..."
        return f"[USER] {preview}"
    if etype == "assistant":
        preview = content[:assistant_preview]
        if len(content) > assistant_preview:
            preview += "..."
        tool_tag = f" tool={tool}" if tool else ""
        return f"[ASSISTANT{tool_tag}] {preview}"
    return None


def _compose_meta(meta: sqlite3.Row, commits: list[str], paths: list[str]) -> list[str]:
    commit_s = ", ".join(c[:8] for c in commits[:10]) or "(none)"
    path_preview = paths[:30]
    more = f" (+{len(paths) - 30} more)" if len(paths) > 30 else ""
    return [
        "[SESSION META]",
        f"session_id: {meta['session_id']}",
        f"project: {meta['project_dir']}",
        f"branch: {meta['git_branch']}",
        f"period: {meta['started_at']} ~ {meta['ended_at']}",
        f"turns: user={meta['user_turn_count']} assistant={meta['assistant_turn_count']} total_events={meta['event_count']}",
        f"related_commits: {commit_s}",
        f"files_touched: {json.dumps(path_preview, ensure_ascii=False)}{more}",
        "",
        "[EVENT TIMELINE]",
    ]


def _assemble(lines: list[str]) -> tuple[str, int]:
    text = "\n".join(lines)
    return text, _estimate_tokens(text)


def _apply_truncation(
    lines_head: list[str],
    turn_lines: list[str],
    *,
    max_tokens: int,
    turns_full: list[sqlite3.Row],
) -> tuple[list[str], int]:
    """head + turn_lines가 max_tokens 초과 시 단계적 절단. 반환: (final_lines, level)."""
    full = lines_head + turn_lines
    _, tok = _assemble(full)
    if tok <= max_tokens:
        return full, 0

    # level 1: assistant 본문 100자로 축소 (turns_full 재포매팅)
    tight = [_format_turn(ev, ASSISTANT_PREVIEW_TIGHT) for ev in turns_full]
    tight = [ln for ln in tight if ln is not None]
    full = lines_head + tight
    _, tok = _assemble(full)
    if tok <= max_tokens:
        return full, 1

    # level 2: 오래된 user 턴부터 제외 (최근 우선). tight 기준으로 뒤에서부터 keep.
    kept: list[str] = []
    running = sum(_estimate_tokens(ln) for ln in lines_head)
    budget = max_tokens - running
    for ln in reversed(tight):
        t = _estimate_tokens(ln)
        if t > budget:
            break
        kept.append(ln)
        budget -= t
    kept.reverse()
    if kept:
        full = lines_head + kept
        _, tok = _assemble(full)
        if tok <= max_tokens:
            return full, 2

    # level 3: 앞뒤 10턴씩 샘플링 + 중간 생략 표시
    head_turns = tight[:SAMPLE_WINDOW]
    tail_turns = tight[-SAMPLE_WINDOW:]
    omitted = len(tight) - len(head_turns) - len(tail_turns)
    sampled = head_turns + [f"[... {omitted} turns omitted ...]"] + tail_turns if omitted > 0 else tight
    full = lines_head + sampled
    return full, 3


def build_prompt(
    conn: sqlite3.Connection,
    session_id: str,
    related_commits: list[str],
    *,
    max_tokens: int = MAX_INPUT_TOKENS,
) -> PromptContext:
    meta = _fetch_session_meta(conn, session_id)
    if meta is None:
        raise ValueError(f"session not found: {session_id}")

    events = _fetch_events(conn, session_id)
    paths = _fetch_snapshot_paths(conn, session_id)

    head = _compose_meta(meta, related_commits, paths)
    turns = [_format_turn(ev, ASSISTANT_PREVIEW_DEFAULT) for ev in events]
    turn_lines = [ln for ln in turns if ln is not None]

    # tight level에서 재포매팅하기 위해 원본 events 중 유효한 것만 추림
    event_rows_kept = [ev for ev, ln in zip(events, turns) if ln is not None]

    final_lines, level = _apply_truncation(
        head, turn_lines, max_tokens=max_tokens, turns_full=event_rows_kept
    )
    user_text, tok = _assemble(final_lines)

    # WHY: 하드 백스톱 — 어떤 절단 단계로도 한도 초과 시 강제 절삭.
    #      단일 초대형 턴 등 예외 입력이 컨텍스트 윈도우(200K)를 넘겨 400을 내던 결함의 최종 방어선.
    if tok > max_tokens:
        user_text = user_text[: max_tokens * 4] + "\n[... truncated to fit context ...]"
        tok = _estimate_tokens(user_text)

    return PromptContext(
        session_id=session_id,
        system=SYSTEM_PROMPT,
        user=user_text,
        est_input_tokens=tok,
        truncation_level=level,
    )
