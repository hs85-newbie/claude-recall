"""session-archive CLI.

usage:
    session-archive ingest [--force]
    session-archive search <query> [--project SLUG] [--since 7d] [--limit 20]
    session-archive show <session_id>
    session-archive stats
    session-archive backup [<path>]
    session-archive summarize [--session ID | --limit N] [--dry-run | --re-eval]
    session-archive export-obsidian [--vault PATH] [--since TS] [--full] [--dry-run]
    session-archive mcp
    session-archive sync-vault [--vault PATH]
    session-archive vault-push [--vault PATH] [--message MSG]
"""
from __future__ import annotations

import argparse
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path

from .checkpoints import ingest_checkpoints
from .compare import compare_candidates
from .db import connect, get_db_path
from .ingest import CLAUDE_PROJECTS_ROOT, ingest_all
from .local_client import ping as local_ping
from .prompt import build_prompt
from .recall import search_checkpoints, search_events
from .summarize import reeval_low_quality, summarize_candidates
from .trigger import iter_candidates


def _cmd_ingest(args: argparse.Namespace) -> int:
    conn = connect()
    t0 = datetime.now()
    root = Path(args.root).expanduser() if args.root else CLAUDE_PROJECTS_ROOT
    stats = ingest_all(conn, root=root, force=args.force)
    dt = (datetime.now() - t0).total_seconds()
    print(f"[ingest] {dt:.1f}s")
    print(f"  scanned={stats.files_scanned}")
    print(f"  processed={stats.files_processed}")
    print(f"  skipped: unchanged={stats.files_skipped_unchanged} empty={stats.files_skipped_empty} trivial={stats.files_skipped_trivial}")
    print(f"  sessions_upserted={stats.sessions_upserted}")
    print(f"  events_upserted={stats.events_upserted}")
    print(f"  snapshots_upserted={stats.snapshots_upserted}")
    print(f"  mask_hits_total={stats.mask_hits_total}")
    print(f"  parse_errors={stats.parse_errors}")
    return 0


def _cmd_search(args: argparse.Namespace) -> int:
    conn = connect()
    t0 = datetime.now()
    rows = search_events(
        conn, args.query,
        project=args.project, since=args.since, limit=args.limit, raw=args.raw,
    )
    dt = (datetime.now() - t0).total_seconds() * 1000
    print(f"[search] {len(rows)} hits ({dt:.0f}ms)")
    for r in rows:
        ts = r["timestamp"][:19] if r["timestamp"] else "?"
        proj = r["project_slug"] or "?"
        role = r["role"] or "?"
        preview = (r["preview"] or "").replace("\n", " ")
        print(f"  {ts} [{proj}] {role}: {preview}")
        print(f"    session={r['session_id']}")

    # WHY: 통합 RAG — 체크포인트(다음 할 일/인계)도 같은 검색에 포함
    cp_rows = search_checkpoints(
        conn, args.query, project=args.project, limit=args.limit, raw=args.raw,
    )
    if cp_rows:
        print(f"[checkpoints] {len(cp_rows)} hits")
        for r in cp_rows:
            ts = (r["created_at"] or "?")[:19]
            preview = (r["preview"] or "").replace("\n", " ")
            print(f"  {ts} [{r['project_slug']}] checkpoint: {r['title']}")
            print(f"    {preview}")
    return 0


def _cmd_mcp(args: argparse.Namespace) -> int:
    """MCP 서버(stdio) 실행 — Claude Code가 과거 세션/결정/체크포인트 회상."""
    from .mcp_server import serve

    serve()
    return 0


def _cmd_sync_vault(args: argparse.Namespace) -> int:
    """vault git pull — 타 머신이 push한 결정·체크포인트 수신(크로스머신 회상 소스)."""
    from .vault_git import vault_pull

    ok, msg = vault_pull(args.vault)
    print(f"[sync-vault] {'ok' if ok else 'FAIL'}: {msg}")
    return 0 if ok else 1


def _cmd_vault_push(args: argparse.Namespace) -> int:
    """로컬 export 변경을 vault에 commit+push — 타 머신 회상용 송신."""
    from .machine import machine_id
    from .vault_git import vault_commit_push

    msg = args.message or f"vault sync {machine_id()} {datetime.now().strftime('%Y-%m-%d %H:%M')}"
    ok, out = vault_commit_push(args.vault, msg)
    print(f"[vault-push] {'ok' if ok else 'FAIL'}: {out}")
    return 0 if ok else 1


def _export_checkpoints_to_vault(conn, vault: Path) -> int:
    """마스킹된 체크포인트를 vault/checkpoints/<machine>/<slug>/ 로 미러 (git 동기화·인계용)."""
    rows = conn.execute(
        "SELECT checkpoint_id, machine, project_slug, content FROM checkpoints"
    ).fetchall()
    n = 0
    for r in rows:
        d = vault / "checkpoints" / (r["machine"] or "unknown") / (r["project_slug"] or "_")
        d.mkdir(parents=True, exist_ok=True)
        fname = re.sub(r"[^A-Za-z0-9_.-]", "-", r["checkpoint_id"].split("::")[-1])
        (d / fname).write_text(r["content"] or "", encoding="utf-8")
        n += 1
    return n


def _cmd_ingest_checkpoints(args: argparse.Namespace) -> int:
    conn = connect()
    t0 = datetime.now()
    stats = ingest_checkpoints(conn, force=args.force)
    dt = (datetime.now() - t0).total_seconds()
    written = _export_checkpoints_to_vault(conn, Path(args.vault).expanduser()) if args.vault else 0
    print(
        f"[ingest-checkpoints] {dt:.1f}s scanned={stats.files_scanned} "
        f"upserted={stats.upserted} skip={stats.skipped_unchanged} "
        f"mask={stats.mask_hits} vault_written={written}"
    )
    if stats.errors:
        print(f"  ⚠️ errors={len(stats.errors)}: {stats.errors[:3]}")
    return 0


def _cmd_show(args: argparse.Namespace) -> int:
    conn = connect()
    s = conn.execute(
        "SELECT * FROM sessions WHERE session_id = ?", (args.session_id,)
    ).fetchone()
    if not s:
        print(f"session not found: {args.session_id}")
        return 1

    print(f"=== session {s['session_id']} ===")
    print(f"project:    {s['project_dir']} ({s['project_slug']})")
    print(f"branch:     {s['git_branch']}")
    print(f"started:    {s['started_at']}")
    print(f"ended:      {s['ended_at']}")
    print(f"events:     total={s['event_count']} user={s['user_turn_count']} assistant={s['assistant_turn_count']}")
    print(f"source:     {s['source_file']}")
    print(f"promoted:   {bool(s['promoted_to_l2'])}")

    if args.timeline:
        print("\n--- timeline ---")
        rows = conn.execute(
            "SELECT timestamp, type, role, tool_name, substr(content,1,160) c FROM events WHERE session_id = ? ORDER BY timestamp",
            (args.session_id,),
        ).fetchall()
        for r in rows:
            ts = (r["timestamp"] or "")[:19]
            tag = r["role"] or r["type"]
            tool = f" {r['tool_name']}" if r["tool_name"] else ""
            content = (r["c"] or "").replace("\n", " ")
            print(f"  {ts} {tag}{tool}: {content}")
    return 0


def _cmd_stats(args: argparse.Namespace) -> int:
    conn = connect()
    db_path = get_db_path()
    size_mb = db_path.stat().st_size / 1024 / 1024 if db_path.exists() else 0

    totals = conn.execute(
        "SELECT COUNT(*) sessions, SUM(event_count) events, SUM(user_turn_count) users, SUM(assistant_turn_count) assistants FROM sessions"
    ).fetchone()
    print(f"=== session-archive stats ===")
    print(f"db:       {db_path} ({size_mb:.1f} MB)")
    print(f"sessions: {totals['sessions'] or 0}")
    print(f"events:   {totals['events'] or 0}")
    print(f"turns:    user={totals['users'] or 0} assistant={totals['assistants'] or 0}")

    print(f"\nby project (top 15):")
    rows = conn.execute(
        """
        SELECT project_slug,
               COUNT(*) sessions,
               SUM(event_count) events,
               MAX(ended_at) last
        FROM sessions
        GROUP BY project_slug
        ORDER BY sessions DESC
        LIMIT 15
        """
    ).fetchall()
    for r in rows:
        last = (r["last"] or "")[:10]
        print(f"  {r['sessions']:4d} sessions / {r['events'] or 0:6d} events / last={last}  {r['project_slug']}")

    mh = conn.execute(
        "SELECT category, SUM(hits) h FROM mask_stats GROUP BY category ORDER BY h DESC"
    ).fetchall()
    if mh:
        print(f"\nmask hits:")
        for r in mh:
            print(f"  {r['h']:6d}  {r['category']}")

    pe = conn.execute("SELECT COUNT(*) c FROM parse_errors").fetchone()["c"]
    if pe:
        print(f"\nparse errors: {pe}  (see parse_errors table)")
    return 0


def _cmd_backup(args: argparse.Namespace) -> int:
    src = get_db_path()
    if not src.exists():
        print(f"db not found: {src}")
        return 1
    if args.path:
        dst = Path(args.path)
    else:
        backups = src.parent / "backups"
        backups.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        dst = backups / f"sessions-{stamp}.db"
    # SQLite online backup API
    import sqlite3

    with sqlite3.connect(str(src)) as s, sqlite3.connect(str(dst)) as d:
        s.backup(d)
    print(f"backup: {dst} ({dst.stat().st_size / 1024 / 1024:.1f} MB)")
    return 0


def _summarize_progress(cand, result) -> None:
    sid_short = cand.session_id[:16]
    if result is None or result.parsed is None:
        err = (result.error if result else "exception") or "unknown"
        print(f"  [FAIL] {sid_short}  reason={cand.reason}  err={err[:80]}", flush=True)
        return
    intent = (result.parsed.get("intent") or "")[:60]
    q = result.parsed.get("quality_score")
    print(
        f"  [{result.model.split('-')[1]}] {sid_short}  q={q}  "
        f"in={result.input_tokens} out={result.output_tokens} ${result.cost_usd:.4f}  {intent}",
        flush=True,
    )


def _compare_progress(cand, result) -> None:
    sid_short = cand.session_id[:16]
    if not isinstance(result, dict) or "error" in result:
        err = result.get("error", "?") if isinstance(result, dict) else str(result)
        print(f"  [FAIL] {sid_short}  err={err[:80]}", flush=True)
        return
    winner = result.get("winner_model") or "-"
    composite = result.get("winner_composite") or 0
    reason = result.get("reason") or ""

    gm, gs = result["gemma"]
    g_line = f"G:schema={gs.schema} comp={gs.composite}" if gs else "G:skip"
    if result.get("haiku"):
        hm, hs = result["haiku"]
        h_line = f"H:schema={hs.schema} comp={hs.composite}"
    else:
        h_line = "H:skip"
    short_winner = (winner or "none").split("-")[1] if winner and "-" in winner else (winner or "-")
    print(
        f"  [{short_winner:>6s}] {sid_short}  comp={composite:<3d}  "
        f"{g_line}  {h_line}  reason={reason[:40]}",
        flush=True,
    )


def _cmd_summarize(args: argparse.Namespace) -> int:
    conn = connect()

    if args.re_eval:
        print(f"[reeval] quality_score < {args.quality_threshold}")
        stats = reeval_low_quality(
            conn, threshold=args.quality_threshold, limit=args.limit
        )
        print(f"\n=== reeval stats ===")
        print(f"candidates:        {stats.candidates}")
        print(f"succeeded:         {stats.succeeded}")
        print(f"failed:            {stats.failed}")
        print(f"skipped_budget:    {stats.skipped_budget}")
        print(f"cost_usd:          ${stats.total_cost_usd:.4f}")
        return 0

    cands = iter_candidates(
        conn, force_session_id=args.session, limit=args.limit
    )
    print(f"[summarize] mode={args.mode} candidates={len(cands)}")

    if args.dry_run:
        for c in cands:
            ctx = build_prompt(conn, c.session_id, c.related_commits)
            proj = (c.project_dir or "")[-50:]
            commits = f" commits={len(c.related_commits)}" if c.related_commits else ""
            print(
                f"  [{c.reason}] {c.session_id[:16]}  turns={c.user_turns}  "
                f"tokens~{ctx.est_input_tokens}  trunc={ctx.truncation_level}{commits}  {proj}"
            )
        if args.mode == "compare":
            ok = local_ping()
            print(f"\n[LM Studio] ping: {'OK' if ok else 'UNREACHABLE'}")
        return 0

    if args.mode == "haiku-only":
        stats = summarize_candidates(conn, cands, on_progress=_summarize_progress)
        print(f"\n=== haiku-only stats ===")
        print(f"candidates:        {stats.candidates}")
        print(f"succeeded:         {stats.succeeded}")
        print(f"failed:            {stats.failed}")
        print(f"retried_sonnet:    {stats.retried_with_sonnet}")
        print(f"cost_usd:          ${stats.total_cost_usd:.4f}")
        return 0

    # compare 모드 (기본)
    cstats = compare_candidates(
        conn, cands,
        force_opus=args.force_opus,
        on_progress=_compare_progress,
        require_local=(args.mode == "compare"),
    )
    print(f"\n=== compare stats ===")
    print(f"candidates:        {cstats.candidates}")
    print(f"gemma_won:         {cstats.gemma_won}")
    print(f"haiku_won:         {cstats.haiku_won}")
    print(f"sonnet_won:        {cstats.sonnet_won}")
    print(f"opus_won:          {cstats.opus_won}")
    print(f"both_failed:       {cstats.both_failed}")
    print(f"opus_quota_blocks: {cstats.opus_quota_blocks}")
    print(f"budget_skips:      {cstats.budget_skips}")
    print(f"cost_usd:          ${cstats.total_cost_usd:.4f}")
    return 0


def _export_progress(row: dict, path) -> None:
    sid_short = (row.get("session_id") or "")[:8]
    proj = (row.get("project_slug") or "")[-30:]
    if path is None:
        print(f"  [skip] {sid_short}  {proj}", flush=True)
    else:
        print(f"  [ok]   {sid_short}  {proj} → {path.name}", flush=True)


def _cmd_export_obsidian(args: argparse.Namespace) -> int:
    from .exporter import export_all

    vault = Path(args.vault).expanduser()
    kinds = tuple(k.strip() for k in args.kinds.split(",") if k.strip())
    print(f"[export-obsidian] vault={vault} kinds={kinds} full={args.full} dry_run={args.dry_run}")
    if args.since:
        print(f"  --since={args.since}")

    conn = connect()
    stats = export_all(
        conn, vault,
        since=args.since,
        full=args.full,
        dry_run=args.dry_run,
        on_progress=_export_progress,
        kinds=kinds,
    )

    print(f"\n=== sessions stats ===")
    print(f"candidates:        {stats.candidates}")
    print(f"written:           {stats.written}")
    print(f"skipped(dry):      {stats.skipped}")
    print(f"failed:            {stats.failed}")
    print(f"last_summarized:   {stats.last_summarized_at or '-'}")
    if stats.by_lang:
        print(f"by_lang:           {dict(stats.by_lang)}")

    if stats.decisions is not None:
        d = stats.decisions
        print(f"\n=== decisions stats ===")
        print(f"candidates(sessions): {d.candidates}")
        print(f"decisions_total:      {d.decisions_total}")
        print(f"written:              {d.written}")
        print(f"skipped:              {d.skipped}")
        print(f"failed:               {d.failed}")
        if d.by_lang:
            print(f"by_lang:              {dict(d.by_lang)}")

    return 0 if stats.failed == 0 else 1


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="session-archive")
    sub = p.add_subparsers(dest="cmd", required=True)

    pi = sub.add_parser("ingest", help="L0 JSONL 증분 적재")
    pi.add_argument("--force", action="store_true", help="mtime 무시하고 전체 재처리")
    pi.add_argument(
        "--root",
        help="Claude Code 로그 디렉토리 (기본: ~/.claude/projects, env SESSION_ARCHIVE_ROOT)",
    )
    pi.set_defaults(func=_cmd_ingest)

    pic = sub.add_parser(
        "ingest-checkpoints", help="gstack 체크포인트 증분 적재(+vault 미러)"
    )
    pic.add_argument("--force", action="store_true", help="mtime 무시하고 전체 재처리")
    pic.add_argument(
        "--vault",
        default="~/llm-wiki",
        help="체크포인트 미러 vault (기본 ~/llm-wiki, 빈 값이면 미러 생략)",
    )
    pic.set_defaults(func=_cmd_ingest_checkpoints)

    ps = sub.add_parser("search", help="FTS 기반 메시지 검색")
    ps.add_argument("query")
    ps.add_argument("--project", help="project_slug LIKE 필터")
    ps.add_argument("--since", help="7d / 24h / 30m")
    ps.add_argument("--limit", type=int, default=20)
    ps.add_argument("--raw", action="store_true", help="FTS5 쿼리 raw 전달 (AND/OR/NOT 직접 제어)")
    ps.set_defaults(func=_cmd_search)

    psh = sub.add_parser("show", help="세션 상세")
    psh.add_argument("session_id")
    psh.add_argument("--timeline", action="store_true", help="이벤트 타임라인 출력")
    psh.set_defaults(func=_cmd_show)

    pst = sub.add_parser("stats", help="적재 현황·프로젝트별 통계")
    pst.set_defaults(func=_cmd_stats)

    pb = sub.add_parser("backup", help="SQLite online backup")
    pb.add_argument("path", nargs="?")
    pb.set_defaults(func=_cmd_backup)

    psum = sub.add_parser("summarize", help="L2 세션 요약 (compare: Gemma4 ∥ Haiku → Sonnet → Opus)")
    psum.add_argument("--mode", choices=["compare", "haiku-only"], default="compare",
                      help="compare(기본)=Gemma4∥Haiku 비교 + 승급 체인, haiku-only=Haiku 단독")
    psum.add_argument("--session", help="특정 session_id 수동 승격")
    psum.add_argument("--limit", type=int, default=None, help="최대 N개 처리")
    psum.add_argument("--dry-run", action="store_true", help="프롬프트만 빌드, API 호출 안 함")
    psum.add_argument("--force-opus", action="store_true", help="compare 모드에서 Opus 직행 (쿼터 체크 유지)")
    psum.add_argument("--re-eval", action="store_true", help="quality_score 낮은 기존 요약을 Sonnet로 재생성")
    psum.add_argument("--quality-threshold", type=int, default=5, help="--re-eval 임계값")
    psum.set_defaults(func=_cmd_summarize)

    pe = sub.add_parser("export-obsidian", help="L2 요약 → Obsidian vault 마크다운 export")
    pe.add_argument("--vault", default="~/llm-wiki", help="vault 디렉터리 (default: ~/llm-wiki)")
    pe.add_argument("--since", help="이 ISO timestamp 이후 summarized_at만 export")
    pe.add_argument("--full", action="store_true", help="watermark 무시, 전체 재export")
    pe.add_argument("--dry-run", action="store_true", help="대상 목록만 출력, 파일 작성 안 함")
    pe.add_argument("--kinds", default="sessions,decisions",
                    help="export 종류 콤마 리스트 (default: sessions,decisions / v1 호환: sessions)")
    pe.set_defaults(func=_cmd_export_obsidian)

    pm = sub.add_parser("mcp", help="MCP 서버(stdio) — Claude Code 자동 회상")
    pm.set_defaults(func=_cmd_mcp)

    psv = sub.add_parser("sync-vault", help="vault git pull — 타 머신 결정·체크포인트 수신")
    psv.add_argument("--vault", default="~/llm-wiki", help="vault 디렉터리 (기본 ~/llm-wiki)")
    psv.set_defaults(func=_cmd_sync_vault)

    pvp = sub.add_parser("vault-push", help="로컬 export를 vault에 commit+push (송신)")
    pvp.add_argument("--vault", default="~/llm-wiki", help="vault 디렉터리 (기본 ~/llm-wiki)")
    pvp.add_argument("--message", help="커밋 메시지 (기본: 머신·시각 자동)")
    pvp.set_defaults(func=_cmd_vault_push)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
