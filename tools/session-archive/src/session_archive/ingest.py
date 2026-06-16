"""L1 적재 파이프라인: scan → filter → parse → mask → upsert.

멱등성:
- sessions.source_file 기준 mtime watermark
- events (session_id, uuid) 복합키 UPSERT
- 서브에이전트 파일은 별도 session으로 취급 (parent::sub::agent-XXX)
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from .mask import mask_text


# WHY: 타 시스템 이식 — Claude Code 로그 위치가 다른 머신은 SESSION_ARCHIVE_ROOT로 override
CLAUDE_PROJECTS_ROOT = Path(
    os.environ.get("SESSION_ARCHIVE_ROOT", str(Path.home() / ".claude" / "projects"))
).expanduser()

MIN_USER_TURNS = 2
MIN_TOTAL_EVENTS = 5


@dataclass
class IngestStats:
    files_scanned: int = 0
    files_processed: int = 0
    files_skipped_empty: int = 0
    files_skipped_unchanged: int = 0
    files_skipped_trivial: int = 0
    sessions_upserted: int = 0
    events_upserted: int = 0
    snapshots_upserted: int = 0
    parse_errors: int = 0
    file_errors: int = 0
    mask_hits_total: int = 0


def scan_session_files(root: Path = CLAUDE_PROJECTS_ROOT) -> Iterator[Path]:
    if not root.exists():
        return
    yield from sorted(root.rglob("*.jsonl"))


def _content_to_text(content) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for p in content:
            if not isinstance(p, dict):
                parts.append(str(p))
                continue
            t = p.get("type")
            if t == "text":
                parts.append(p.get("text", ""))
            elif t == "tool_use":
                name = p.get("name", "?")
                inp = p.get("input")
                inp_s = json.dumps(inp, ensure_ascii=False) if inp else ""
                parts.append(f"[tool_use:{name}] {inp_s}")
            elif t == "tool_result":
                body = p.get("content")
                body_text = body if isinstance(body, str) else json.dumps(body, ensure_ascii=False)
                if len(body_text) > 2048:
                    body_text = body_text[:2048] + "...[truncated]"
                parts.append(f"[tool_result] {body_text}")
            else:
                parts.append(json.dumps(p, ensure_ascii=False))
        return "\n".join(parts)
    return json.dumps(content, ensure_ascii=False)


def _path_to_project_slug(cwd: str) -> str:
    return cwd.replace("/", "-")


def _estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


def _hash_content(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()[:16]


def _derive_session_id(path: Path, raw_session_id: str | None) -> str:
    """서브에이전트 파일은 parent::sub::agent-XXX 형태로 합성.

    경로 패턴: .../<parent_sessionId>/subagents/agent-XXX.jsonl
    """
    parent_dir = path.parent.name
    if parent_dir == "subagents":
        # parent_session_id는 path.parent.parent.name
        parent_sid = path.parent.parent.name
        return f"{parent_sid}::sub::{path.stem}"
    # 정규 파일: raw_session_id 우선, 없으면 stem
    return raw_session_id or path.stem


def _extract_snapshot_meta(ev: dict) -> dict:
    snap = ev.get("snapshot") or {}
    tracked = snap.get("trackedFileBackups") if isinstance(snap, dict) else None
    paths: list[str] = []
    if isinstance(tracked, dict):
        paths = list(tracked.keys())
    return {
        "message_id": ev.get("messageId") or "",
        "timestamp": (snap.get("timestamp") if isinstance(snap, dict) else None) or "",
        "is_update": 1 if ev.get("isSnapshotUpdate") else 0,
        "tracked_count": len(paths),
        "file_paths_json": json.dumps(paths, ensure_ascii=False) if paths else None,
    }


@dataclass
class _ParsedFile:
    session_id: str
    project_dir: str
    project_slug: str
    started_at: str | None
    ended_at: str | None
    git_branch: str | None
    user_turns: int = 0
    assistant_turns: int = 0
    event_rows: list[tuple] = field(default_factory=list)
    fts_rows: list[tuple] = field(default_factory=list)
    snapshot_rows: list[tuple] = field(default_factory=list)
    mask_hits: dict[str, int] = field(default_factory=dict)
    last_uuid: str | None = None


def _parse_file(path: Path, stats: IngestStats) -> _ParsedFile | None:
    """파일 전체 파싱 후 in-memory 구조 반환. DB 쓰기는 호출자가 처리."""
    # 1) raw lines → event dicts
    events: list[dict] = []
    with path.open("r", encoding="utf-8", errors="replace") as f:
        for idx, raw in enumerate(f, start=1):
            raw = raw.strip()
            if not raw:
                continue
            try:
                events.append(json.loads(raw))
            except json.JSONDecodeError as e:
                stats.parse_errors += 1
                # parse_errors는 나중에 일괄 insert하지 않고 호출자가 알아서 처리
                continue
    if not events:
        return None

    # 2) session 메타 추출 — 첫 번째 유효 이벤트에서
    raw_sid = None
    cwd = None
    git_branch = None
    started_at = None
    for ev in events:
        if ev.get("type") in ("queue-operation", "file-history-snapshot"):
            continue
        raw_sid = ev.get("sessionId")
        cwd = ev.get("cwd")
        git_branch = ev.get("gitBranch")
        started_at = ev.get("timestamp")
        if raw_sid and started_at:
            break

    session_id = _derive_session_id(path, raw_sid)
    project_dir = cwd or "(unknown)"
    project_slug = _path_to_project_slug(project_dir)

    parsed = _ParsedFile(
        session_id=session_id,
        project_dir=project_dir,
        project_slug=project_slug,
        started_at=started_at,
        ended_at=started_at,
        git_branch=git_branch,
    )

    # 3) 이벤트별 행 생성
    for ev in events:
        etype = ev.get("type")
        ts = ev.get("timestamp")
        if ts:
            parsed.ended_at = ts

        if etype == "file-history-snapshot":
            meta = _extract_snapshot_meta(ev)
            if not meta["message_id"]:
                continue
            parsed.snapshot_rows.append(
                (
                    session_id,
                    meta["message_id"],
                    meta["timestamp"] or parsed.ended_at or "",
                    meta["is_update"],
                    meta["tracked_count"],
                    meta["file_paths_json"],
                )
            )
            continue

        if etype in ("queue-operation",):
            continue

        uuid = ev.get("uuid")
        if not uuid:
            continue

        if etype == "user":
            parsed.user_turns += 1
        elif etype == "assistant":
            parsed.assistant_turns += 1

        msg = ev.get("message") or {}
        if not isinstance(msg, dict):
            msg = {}
        role = msg.get("role")
        raw_content = msg.get("content")
        text = _content_to_text(raw_content)

        tool_name = None
        if isinstance(raw_content, list):
            for p in raw_content:
                if isinstance(p, dict) and p.get("type") == "tool_use":
                    tool_name = p.get("name")
                    break

        mr = mask_text(text)
        if mr.masked:
            for cat, n in mr.hits.items():
                parsed.mask_hits[cat] = parsed.mask_hits.get(cat, 0) + n

        parsed.event_rows.append(
            (
                session_id,
                uuid,
                ev.get("parentUuid"),
                etype,
                ts or "",
                role,
                mr.text,
                _hash_content(mr.text),
                tool_name,
                ev.get("cwd"),
                ev.get("gitBranch"),
                1 if mr.masked else 0,
                _estimate_tokens(mr.text),
            )
        )
        parsed.last_uuid = uuid

        if role in ("user", "assistant") and mr.text:
            parsed.fts_rows.append((uuid, session_id, mr.text))

    # total events (user + assistant + others except queue/snapshot)
    total_events = len(parsed.event_rows)
    if parsed.user_turns < MIN_USER_TURNS or total_events < MIN_TOTAL_EVENTS:
        return None  # trivial

    return parsed


def _flush_parsed(conn: sqlite3.Connection, parsed: _ParsedFile, path: Path, mtime: float) -> None:
    """단일 트랜잭션으로 session + events + snapshots + fts + mask_stats 기록."""
    conn.execute("BEGIN")
    try:
        conn.execute(
            """
            INSERT INTO sessions (
                session_id, project_dir, project_slug, started_at, ended_at,
                event_count, user_turn_count, assistant_turn_count, git_branch,
                source_file, source_mtime, source_last_uuid, promoted_to_l2
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
            ON CONFLICT(session_id) DO UPDATE SET
                project_dir = excluded.project_dir,
                project_slug = excluded.project_slug,
                started_at = excluded.started_at,
                ended_at = excluded.ended_at,
                event_count = excluded.event_count,
                user_turn_count = excluded.user_turn_count,
                assistant_turn_count = excluded.assistant_turn_count,
                git_branch = excluded.git_branch,
                source_file = excluded.source_file,
                source_mtime = excluded.source_mtime,
                source_last_uuid = excluded.source_last_uuid
            """,
            (
                parsed.session_id,
                parsed.project_dir,
                parsed.project_slug,
                parsed.started_at or parsed.ended_at or "",
                parsed.ended_at or parsed.started_at or "",
                len(parsed.event_rows),
                parsed.user_turns,
                parsed.assistant_turns,
                parsed.git_branch,
                str(path),
                mtime,
                parsed.last_uuid,
            ),
        )

        if parsed.event_rows:
            conn.executemany(
                """
                INSERT INTO events (
                    session_id, uuid, parent_uuid, type, timestamp,
                    role, content, content_hash, tool_name, cwd, git_branch, masked, token_count
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(session_id, uuid) DO UPDATE SET
                    parent_uuid = excluded.parent_uuid,
                    type = excluded.type,
                    timestamp = excluded.timestamp,
                    role = excluded.role,
                    content = excluded.content,
                    content_hash = excluded.content_hash,
                    tool_name = excluded.tool_name,
                    cwd = excluded.cwd,
                    git_branch = excluded.git_branch,
                    masked = excluded.masked,
                    token_count = excluded.token_count
                """,
                parsed.event_rows,
            )

        if parsed.snapshot_rows:
            conn.executemany(
                """
                INSERT INTO file_snapshots (session_id, message_id, timestamp, is_update, tracked_count, file_paths_json)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(session_id, message_id) DO UPDATE SET
                    timestamp = excluded.timestamp,
                    is_update = excluded.is_update,
                    tracked_count = excluded.tracked_count,
                    file_paths_json = excluded.file_paths_json
                """,
                parsed.snapshot_rows,
            )

        if parsed.fts_rows:
            # FTS는 unique 제약이 없으므로 기존 행 삭제 후 삽입
            conn.executemany(
                "DELETE FROM events_fts WHERE uuid = ? AND session_id = ?",
                [(r[0], r[1]) for r in parsed.fts_rows],
            )
            conn.executemany(
                "INSERT INTO events_fts (uuid, session_id, content) VALUES (?, ?, ?)",
                parsed.fts_rows,
            )

        if parsed.mask_hits:
            conn.execute("DELETE FROM mask_stats WHERE session_id = ?", (parsed.session_id,))
            conn.executemany(
                "INSERT INTO mask_stats (session_id, category, hits) VALUES (?, ?, ?)",
                [(parsed.session_id, cat, n) for cat, n in parsed.mask_hits.items()],
            )

        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise


def ingest_file(
    conn: sqlite3.Connection,
    path: Path,
    *,
    force: bool = False,
    stats: IngestStats | None = None,
) -> IngestStats:
    stats = stats or IngestStats()
    stats.files_scanned += 1

    if not path.exists():
        return stats

    size = path.stat().st_size
    if size == 0:
        stats.files_skipped_empty += 1
        return stats
    mtime = path.stat().st_mtime

    if not force:
        row = conn.execute(
            "SELECT source_mtime FROM sessions WHERE source_file = ? ORDER BY source_mtime DESC LIMIT 1",
            (str(path),),
        ).fetchone()
        if row and row["source_mtime"] >= mtime:
            stats.files_skipped_unchanged += 1
            return stats

    try:
        parsed = _parse_file(path, stats)
    except Exception as e:
        stats.file_errors += 1
        conn.execute(
            "INSERT INTO parse_errors (source_file, line_no, error, raw, seen_at) VALUES (?, ?, ?, ?, ?)",
            (str(path), 0, f"{type(e).__name__}: {e}", None, datetime.now(timezone.utc).isoformat()),
        )
        return stats

    if parsed is None:
        stats.files_skipped_trivial += 1
        return stats

    try:
        _flush_parsed(conn, parsed, path, mtime)
    except Exception as e:
        stats.file_errors += 1
        conn.execute(
            "INSERT INTO parse_errors (source_file, line_no, error, raw, seen_at) VALUES (?, ?, ?, ?, ?)",
            (str(path), 0, f"{type(e).__name__}: {e}", None, datetime.now(timezone.utc).isoformat()),
        )
        return stats

    stats.sessions_upserted += 1
    stats.events_upserted += len(parsed.event_rows)
    stats.snapshots_upserted += len(parsed.snapshot_rows)
    stats.mask_hits_total += sum(parsed.mask_hits.values())
    stats.files_processed += 1
    return stats


def ingest_all(
    conn: sqlite3.Connection,
    root: Path = CLAUDE_PROJECTS_ROOT,
    *,
    force: bool = False,
    progress_every: int = 50,
) -> IngestStats:
    stats = IngestStats()
    for i, path in enumerate(scan_session_files(root), start=1):
        ingest_file(conn, path, force=force, stats=stats)
        if progress_every and i % progress_every == 0:
            print(
                f"  progress: {i} files (processed={stats.files_processed} "
                f"sessions={stats.sessions_upserted} events={stats.events_upserted})",
                flush=True,
            )
    return stats
