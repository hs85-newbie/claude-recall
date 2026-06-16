"""체크포인트 적재 단위 테스트."""
import sqlite3

from session_archive.db import SCHEMA_SQL
from session_archive.checkpoints import ingest_checkpoints, _parse_meta, _machine


def _mk_db():
    conn = sqlite3.connect(":memory:", isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA_SQL.read_text(encoding="utf-8"))
    return conn


def _seed_checkpoints(root, slug="proj-a", files=None):
    cp_dir = root / slug / "checkpoints"
    cp_dir.mkdir(parents=True)
    for name, body in (files or {}).items():
        (cp_dir / name).write_text(body, encoding="utf-8")
    return cp_dir


def test_parse_meta_filename_timestamp(tmp_path):
    p = tmp_path / "myproj" / "checkpoints" / "20260507-125318-publish-policy-인계.md"
    p.parent.mkdir(parents=True)
    p.write_text("x")
    created, title, slug = _parse_meta(p)
    assert created == "2026-05-07T12:53:18Z"
    assert title == "publish-policy-인계"
    assert slug == "myproj"


def test_ingest_checkpoints_upserts_and_indexes(tmp_path):
    conn = _mk_db()
    _seed_checkpoints(tmp_path, "proj-a", {
        "20260507-125318-handoff.md": "## NEXT\n다음 작업: 토큰 대시보드 구현",
        "20260508-101010-other.md": "결정: STT는 Clova로 고정",
    })

    stats = ingest_checkpoints(conn, root=tmp_path)

    assert stats.files_scanned == 2
    assert stats.upserted == 2
    # FTS 검색 가능
    hits = conn.execute(
        "SELECT checkpoint_id FROM checkpoints_fts WHERE checkpoints_fts MATCH ?",
        ("토큰",),
    ).fetchall()
    assert len(hits) == 1


def test_ingest_checkpoints_incremental_skip(tmp_path):
    conn = _mk_db()
    _seed_checkpoints(tmp_path, "proj-a", {"20260507-125318-h.md": "내용"})
    ingest_checkpoints(conn, root=tmp_path)
    # 두 번째 실행 — 변경 없으면 skip
    stats2 = ingest_checkpoints(conn, root=tmp_path)
    assert stats2.upserted == 0
    assert stats2.skipped_unchanged == 1


def test_ingest_checkpoints_masks_secrets(tmp_path):
    conn = _mk_db()
    _seed_checkpoints(tmp_path, "proj-a", {
        "20260507-125318-leak.md": "키: sk-ant-abcdefghijklmnopqrstuvwxyz0123456789",
    })
    ingest_checkpoints(conn, root=tmp_path)
    row = conn.execute("SELECT content FROM checkpoints").fetchone()
    assert "sk-ant-abcdefghijklmnopqrstuvwxyz0123456789" not in row["content"]


def test_machine_id_is_path_safe(monkeypatch):
    monkeypatch.setenv("SESSION_ARCHIVE_MACHINE", "My Laptop!/x")
    assert _machine() == "My-Laptop--x"
