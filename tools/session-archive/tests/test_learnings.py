"""learnings export(오답노트) 단위 테스트."""
from __future__ import annotations

import json
from pathlib import Path

from session_archive import learnings, recall


def _seed_learnings(root: Path, slug: str, items: list[dict]) -> Path:
    """`<root>/<slug>/learnings.jsonl` 생성."""
    d = root / slug
    d.mkdir(parents=True, exist_ok=True)
    p = d / "learnings.jsonl"
    p.write_text(
        "\n".join(json.dumps(it, ensure_ascii=False) for it in items) + "\n",
        encoding="utf-8",
    )
    return p


# --- _parse_jsonl ---

def test_parse_jsonl_빈줄과_깨진줄을_건너뛴다(tmp_path: Path) -> None:
    p = tmp_path / "learnings.jsonl"
    p.write_text(
        '{"key":"a","insight":"x"}\n'
        "\n"
        "  \n"
        "{not valid json}\n"
        '{"key":"b","insight":"y"}\n'
        '["배열은 dict 아님"]\n',
        encoding="utf-8",
    )
    out = learnings._parse_jsonl(p)
    assert [o["key"] for o in out] == ["a", "b"]


# --- scan_learnings_files ---

def test_scan_learnings_files_프로젝트별_jsonl만_찾는다(tmp_path: Path) -> None:
    _seed_learnings(tmp_path, "proj-a", [{"key": "k1", "insight": "i1"}])
    _seed_learnings(tmp_path, "proj-b", [{"key": "k2", "insight": "i2"}])
    (tmp_path / "proj-a" / "other.jsonl").write_text("{}\n", encoding="utf-8")
    found = [p.parent.name for p in learnings.scan_learnings_files(tmp_path)]
    assert found == ["proj-a", "proj-b"]


def test_scan_learnings_files_루트없으면_빈순회(tmp_path: Path) -> None:
    assert list(learnings.scan_learnings_files(tmp_path / "nope")) == []


# --- render_markdown ---

def test_render_markdown_front_matter와_insight_포함(tmp_path: Path) -> None:
    md = learnings.render_markdown(
        "proj-a",
        "machineX",
        [{"key": "grep-over-fts", "type": "pattern", "confidence": 8,
          "source": "observed", "insight": "작은 코퍼스는 grep이 단순하다.",
          "files": ["a.py"], "ts": "2026-06-16T00:00:00Z"}],
    )
    assert md.startswith("---\n")
    assert 'machine: "machineX"' in md
    assert "kind: learnings" in md
    assert "grep-over-fts" in md
    assert "confidence 8" in md
    assert "작은 코퍼스는 grep이 단순하다." in md
    assert "a.py" in md


def test_render_markdown_ts_내림차순_최신우선(tmp_path: Path) -> None:
    md = learnings.render_markdown(
        "p",
        "m",
        [
            {"key": "old", "insight": "옛것", "ts": "2026-01-01T00:00:00Z"},
            {"key": "new", "insight": "새것", "ts": "2026-06-01T00:00:00Z"},
        ],
    )
    assert md.index("new") < md.index("old")


# --- export_learnings ---

def test_export_learnings_vault에_머신_네임스페이스로_기록(tmp_path: Path) -> None:
    gstack = tmp_path / "gstack"
    vault = tmp_path / "vault"
    _seed_learnings(gstack, "proj-a", [{"key": "k1", "insight": "교훈1"}])
    stats = learnings.export_learnings(vault, root=gstack, machine="machineX")
    out = vault / "learnings" / "machineX" / "proj-a.md"
    assert out.exists()
    assert stats.projects_written == 1
    assert stats.learnings_total == 1
    assert "교훈1" in out.read_text(encoding="utf-8")


def test_export_learnings_빈_jsonl은_건너뛴다(tmp_path: Path) -> None:
    gstack = tmp_path / "gstack"
    vault = tmp_path / "vault"
    (gstack / "empty").mkdir(parents=True)
    (gstack / "empty" / "learnings.jsonl").write_text("\n  \n", encoding="utf-8")
    stats = learnings.export_learnings(vault, root=gstack, machine="m")
    assert stats.files_scanned == 1
    assert stats.projects_written == 0
    assert not (vault / "learnings" / "m" / "empty.md").exists()


def test_export_learnings_멱등_재생성(tmp_path: Path) -> None:
    gstack = tmp_path / "gstack"
    vault = tmp_path / "vault"
    _seed_learnings(gstack, "p", [{"key": "k", "insight": "v1"}])
    learnings.export_learnings(vault, root=gstack, machine="m")
    # 같은 슬러그를 새 내용으로 다시 export → 덮어쓰기(단일 작성자)
    _seed_learnings(gstack, "p", [{"key": "k", "insight": "v2"}])
    learnings.export_learnings(vault, root=gstack, machine="m")
    text = (vault / "learnings" / "m" / "p.md").read_text(encoding="utf-8")
    assert "v2" in text
    assert "v1" not in text


def test_export_learnings_시크릿_마스킹(tmp_path: Path) -> None:
    gstack = tmp_path / "gstack"
    vault = tmp_path / "vault"
    _seed_learnings(
        gstack, "p",
        [{"key": "leak", "insight": "키 노출 사례: AKIAIOSFODNN7EXAMPLE 였다."}],
    )
    stats = learnings.export_learnings(vault, root=gstack, machine="m")
    text = (vault / "learnings" / "m" / "p.md").read_text(encoding="utf-8")
    assert "AKIAIOSFODNN7EXAMPLE" not in text
    assert stats.mask_hits >= 1


# --- search_vault 통합: learnings kind 포함 ---

def test_search_vault가_learnings를_회상한다(tmp_path: Path) -> None:
    gstack = tmp_path / "gstack"
    vault = tmp_path / "vault"
    _seed_learnings(
        gstack, "proj-a",
        [{"key": "grep-over-fts", "insight": "작은 코퍼스는 grep이 단순하다."}],
    )
    learnings.export_learnings(vault, root=gstack, machine="machineX")
    hits = recall.search_vault("grep 코퍼스", vault_root=vault)
    assert len(hits) == 1
    assert hits[0]["kind"] == "learnings"
    assert hits[0]["machine"] == "machineX"


def test_search_vault_kind_learnings_필터(tmp_path: Path) -> None:
    gstack = tmp_path / "gstack"
    vault = tmp_path / "vault"
    _seed_learnings(gstack, "p", [{"key": "k", "insight": "오답 회상 테스트"}])
    learnings.export_learnings(vault, root=gstack, machine="m")
    hits = recall.search_vault("오답", vault_root=vault, kind="learnings")
    assert len(hits) == 1
    assert hits[0]["kind"] == "learnings"
