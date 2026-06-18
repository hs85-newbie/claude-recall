"""읽기 전용 회상 질의 — MCP 서버와 CLI search가 공유하는 순수 질의 계층.

세션 이벤트(FTS)·체크포인트(FTS)·L2 결정(session_summaries)을 조회한다.
부수효과 없음(읽기 전용), Anthropic API 미사용 — stdlib + sqlite3만 사용한다.

WHY: cli.py의 검색 SQL을 여기로 추출해 MCP 서버와 단일 구현을 공유한다(DRY).
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .db import get_db_path

# 크로스머신 회상: pull된 vault(~/llm-wiki) 마크다운을 직접 grep한다(DB 없음).
# 하이브리드 설계 — vault는 이미 마스킹된 L2라 색인/재마스킹 없이 읽기만.
DEFAULT_VAULT_ROOT = Path(
    os.environ.get("SESSION_ARCHIVE_VAULT", str(Path.home() / "llm-wiki"))
).expanduser()
_VAULT_KINDS = ("sessions", "decisions", "checkpoints", "learnings")

_FTS_OPS = {"AND", "OR", "NOT", "NEAR"}
_SINCE_RE = re.compile(r"^(\d+)([dhm])$")


def sanitize_fts_query(q: str) -> str:
    """FTS5 쿼리 안전 변환.

    - 대문자 연산자(AND/OR/NOT/NEAR)는 그대로 둔다
    - 그 외 토큰은 영숫자/한글 only가 아니면 "..." 로 감싼다
    - 내부 큰따옴표는 두 개로 escape
    """
    out: list[str] = []
    for tok in q.split():
        if tok in _FTS_OPS:
            out.append(tok)
            continue
        if re.fullmatch(r"[\w가-힣]+", tok):
            out.append(tok)
        else:
            out.append('"' + tok.replace('"', '""') + '"')
    return " ".join(out)


def parse_since(spec: str | None) -> str | None:
    """'7d' / '24h' / '30m' → 그만큼 과거의 ISO timestamp. None이면 None."""
    if not spec:
        return None
    m = _SINCE_RE.match(spec)
    if not m:
        raise ValueError(f"invalid since format: {spec} (e.g., 7d, 24h, 30m)")
    n = int(m.group(1))
    unit = m.group(2)
    delta = {"d": timedelta(days=n), "h": timedelta(hours=n), "m": timedelta(minutes=n)}[unit]
    return (datetime.now(timezone.utc) - delta).isoformat()


def connect_ro(db_path: Path | None = None) -> sqlite3.Connection:
    """읽기 전용 SQLite 연결.

    @raises FileNotFoundError DB 파일이 아직 존재하지 않을 때(파이프라인 미실행).
    """
    path = db_path or get_db_path()
    if not path.exists():
        raise FileNotFoundError(str(path))
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def search_events(
    conn: sqlite3.Connection,
    query: str,
    *,
    project: str | None = None,
    since: str | None = None,
    limit: int = 20,
    raw: bool = False,
) -> list[dict]:
    """events_fts MATCH 검색. 최신순."""
    fts_query = query if raw else sanitize_fts_query(query)
    since_iso = parse_since(since)
    sql = """
        SELECT e.uuid, e.session_id, e.timestamp, e.role,
               substr(e.content, 1, 200) AS preview,
               s.project_slug, s.project_dir
        FROM events_fts f
        JOIN events e ON e.uuid = f.uuid AND e.session_id = f.session_id
        JOIN sessions s ON s.session_id = e.session_id
        WHERE events_fts MATCH ?
    """
    params: list = [fts_query]
    if project:
        sql += " AND s.project_slug LIKE ?"
        params.append(f"%{project}%")
    if since_iso:
        sql += " AND e.timestamp >= ?"
        params.append(since_iso)
    sql += " ORDER BY e.timestamp DESC LIMIT ?"
    params.append(limit)
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


def search_checkpoints(
    conn: sqlite3.Connection,
    query: str,
    *,
    project: str | None = None,
    limit: int = 20,
    raw: bool = False,
) -> list[dict]:
    """checkpoints_fts MATCH 검색 ("다음 할 일/인계" 소스). 최신순."""
    fts_query = query if raw else sanitize_fts_query(query)
    sql = """
        SELECT c.checkpoint_id, c.machine, c.project_slug, c.title, c.created_at,
               substr(c.content, 1, 300) AS preview
        FROM checkpoints_fts f
        JOIN checkpoints c ON c.checkpoint_id = f.checkpoint_id
        WHERE checkpoints_fts MATCH ?
    """
    params: list = [fts_query]
    if project:
        sql += " AND c.project_slug LIKE ?"
        params.append(f"%{project}%")
    sql += " ORDER BY c.created_at DESC LIMIT ?"
    params.append(limit)
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


def search_history(
    conn: sqlite3.Connection,
    query: str,
    *,
    project: str | None = None,
    since: str | None = None,
    limit: int = 20,
) -> dict:
    """이벤트 + 체크포인트 통합 검색. {"events": [...], "checkpoints": [...]}."""
    return {
        "events": search_events(conn, query, project=project, since=since, limit=limit),
        "checkpoints": search_checkpoints(conn, query, project=project, limit=limit),
    }


def recall_decisions(
    conn: sqlite3.Connection,
    *,
    query: str | None = None,
    project: str | None = None,
    limit: int = 20,
) -> list[dict]:
    """L2 요약의 결정(decisions_json)을 펼쳐 반환. 최신 요약순.

    decisions_json은 [{"decision": str, "rationale": str}, ...] 구조.
    query가 주어지면 decision/rationale 부분 문자열(대소문자 무시)로 필터링한다
    (decisions는 FTS 대상이 아니므로 Python에서 필터).
    """
    sql = """
        SELECT ss.session_id, ss.decisions_json, ss.summarized_at,
               ss.quality_score, s.project_slug
        FROM session_summaries ss
        JOIN sessions s USING (session_id)
        WHERE ss.decisions_json IS NOT NULL AND ss.decisions_json != '[]'
    """
    params: list = []
    if project:
        sql += " AND s.project_slug LIKE ?"
        params.append(f"%{project}%")
    sql += " ORDER BY ss.summarized_at DESC"
    rows = conn.execute(sql, params).fetchall()

    needle = query.lower() if query else None
    out: list[dict] = []
    for r in rows:
        try:
            decisions = json.loads(r["decisions_json"] or "[]")
        except json.JSONDecodeError:
            continue
        for d in decisions:
            if not isinstance(d, dict):
                continue
            decision = (d.get("decision") or "").strip()
            rationale = (d.get("rationale") or "").strip()
            if not decision:
                continue
            if needle and needle not in f"{decision} {rationale}".lower():
                continue
            out.append({
                "decision": decision,
                "rationale": rationale,
                "project_slug": r["project_slug"],
                "summarized_at": r["summarized_at"],
                "quality_score": r["quality_score"],
                "session_id": r["session_id"],
            })
            if len(out) >= limit:
                return out
    return out


def recent_checkpoints(
    conn: sqlite3.Connection,
    *,
    project: str | None = None,
    limit: int = 10,
) -> list[dict]:
    """최신 체크포인트 N개 ("어디까지 했더라"). created_at DESC."""
    sql = """
        SELECT checkpoint_id, machine, project_slug, title, created_at,
               substr(content, 1, 600) AS preview
        FROM checkpoints
    """
    params: list = []
    if project:
        sql += " WHERE project_slug LIKE ?"
        params.append(f"%{project}%")
    sql += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


# ── 크로스머신 vault grep (DB 불필요) ──


def _parse_frontmatter_field(text: str, field: str) -> str | None:
    """선두 `---` front-matter 블록에서 단일 스칼라 필드 추출. 없으면 None."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return None
    for line in lines[1:]:
        if line.strip() == "---":
            break
        key, sep, val = line.partition(":")
        if sep and key.strip() == field:
            return val.strip().strip('"').strip("'") or None
    return None


def _vault_machine(kind: str, rel_parts: tuple[str, ...], text: str) -> str:
    """vault 파일의 머신 라벨. checkpoints=경로, sessions/decisions=front-matter, 없으면 unknown."""
    if kind == "checkpoints" and len(rel_parts) > 1:
        # checkpoints/<machine>/<slug>/<file>.md
        return rel_parts[1] or "unknown"
    return _parse_frontmatter_field(text, "machine") or "unknown"


def _vault_title(text: str, path: Path) -> str:
    """첫 마크다운 헤딩, 없으면 파일명 stem."""
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("# "):
            return s[2:].strip()
        if s.startswith("## "):
            return s[3:].strip()
    return path.stem


def _snippet(text: str, tokens: list[str], context: int) -> str:
    """토큰이 처음 등장하는 줄 ±context 줄을 스니펫으로."""
    lines = text.splitlines()
    for i, line in enumerate(lines):
        low = line.lower()
        if any(t in low for t in tokens):
            a = max(0, i - context)
            b = min(len(lines), i + context + 1)
            return "\n".join(lines[a:b]).strip()
    return ""


def search_vault(
    query: str,
    *,
    vault_root: Path | str | None = None,
    machine: str | None = None,
    kind: str | None = None,
    limit: int = 20,
    context_lines: int = 2,
) -> list[dict]:
    """pull된 vault 마크다운을 grep — 타 머신의 마스킹 L2(결정·체크포인트·요약) 회상.

    모든 query 토큰을 (대소문자 무시) 포함하는 파일을 찾아 스니펫+경로+machine+kind를 반환한다.
    DB·색인 불필요(파일 직독). vault가 없으면 빈 리스트.

    @param machine 머신 라벨 필터(선택, 정확 일치)
    @param kind sessions|decisions|checkpoints 중 하나로 제한(선택)
    """
    root = Path(vault_root).expanduser() if vault_root else DEFAULT_VAULT_ROOT
    if not root.exists():
        return []
    tokens = [t.lower() for t in query.split() if t]
    if not tokens:
        return []
    kinds = (kind,) if kind in _VAULT_KINDS else _VAULT_KINDS
    out: list[dict] = []
    for k in kinds:
        kdir = root / k
        if not kdir.is_dir():
            continue
        for p in sorted(kdir.rglob("*.md")):
            try:
                text = p.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            low = text.lower()
            if not all(t in low for t in tokens):
                continue
            rel = p.relative_to(root)
            mlabel = _vault_machine(k, rel.parts, text)
            if machine and mlabel != machine:
                continue
            out.append({
                "kind": k,
                "machine": mlabel,
                "path": str(rel),
                "title": _vault_title(text, p),
                "snippet": _snippet(text, tokens, context_lines),
            })
            if len(out) >= limit:
                return out
    return out
