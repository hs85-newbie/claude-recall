"""exporter/frontmatter 단위 테스트."""
from __future__ import annotations

import pytest

from session_archive.exporter.frontmatter import (
    build_frontmatter,
    detect_lang,
    normalize_tag,
    normalize_tags,
    serialize,
)


# --- detect_lang ---

@pytest.mark.parametrize(
    "text,expected",
    [
        ("tms-stt OCR 정책 결정. 화자분리는 CMI에서 처리.", "ko"),
        ("이슈 분석 결과: STT pipeline에서 OCR 통합 정책 검토.", "ko"),
        ("プロジェクトの方針を決定しました。", "ja"),
        ("Decided to drop OCR for STT pipeline.", "en"),
        ("决定采用新的 OCR 策略来处理。", "zh"),
        ("ok", "und"),
        ("", "und"),
        ("    ", "und"),
    ],
)
def test_detect_lang(text: str, expected: str) -> None:
    assert detect_lang(text) == expected


# --- normalize_tag(s) ---

def test_normalize_tag_lowercase_and_dash() -> None:
    assert normalize_tag("TMS STT") == "tms-stt"


def test_normalize_tag_strip_special() -> None:
    assert normalize_tag("api/v1!@#") == "apiv1"


def test_normalize_tag_korean_preserved() -> None:
    assert normalize_tag("정책") == "정책"


def test_normalize_tags_dedupe_keep_order() -> None:
    assert normalize_tags(["TMS", "tms", "Policy ", "policy", "정책"]) == [
        "tms",
        "policy",
        "정책",
    ]


def test_normalize_tags_filters_empty() -> None:
    assert normalize_tags(["", "  ", "@#$"]) == []


# --- build_frontmatter ---

@pytest.fixture
def sample_session() -> dict:
    return {
        "session_id": "83351383-6381-42c3-b6b6-3cd393e1d043",
        "project_slug": "-Users-cjons-tms-stt",
        "git_branch": "dev",
    }


@pytest.fixture
def sample_summary() -> dict:
    return {
        "intent": "tms-stt OCR 정책 결정",
        "outcome": "OCR 유지 결정 + CMI 분리",
        "model": "claude-haiku-4-5-20251001",
        "quality_score": 8,
        "summarized_at": "2026-04-25T14:30:00Z",
        "tags_json": '["tms-stt", "STT", "policy"]',
        "files_touched_json": '["src/a.ts", "src/b.ts"]',
    }


def test_build_has_12_fields(sample_session: dict, sample_summary: dict) -> None:
    fm = build_frontmatter(sample_session, sample_summary)
    assert set(fm.keys()) == {
        "session_id",
        "machine",
        "project",
        "branch",
        "summarized_at",
        "model",
        "quality_score",
        "summary_level",
        "kind",
        "lang",
        "tags",
        "files_touched",
    }


def test_build_includes_machine(sample_session: dict, sample_summary: dict, monkeypatch) -> None:
    monkeypatch.setenv("SESSION_ARCHIVE_MACHINE", "test-laptop")
    fm = build_frontmatter(sample_session, sample_summary)
    assert fm["machine"] == "test-laptop"


def test_build_fixed_summary_level_and_kind(sample_session: dict, sample_summary: dict) -> None:
    fm = build_frontmatter(sample_session, sample_summary)
    assert fm["summary_level"] == "L2"
    assert fm["kind"] == "session"


def test_build_lang_korean(sample_session: dict, sample_summary: dict) -> None:
    fm = build_frontmatter(sample_session, sample_summary)
    assert fm["lang"] == "ko"


def test_build_tags_normalized(sample_session: dict, sample_summary: dict) -> None:
    fm = build_frontmatter(sample_session, sample_summary)
    assert fm["tags"] == ["tms-stt", "stt", "policy"]


def test_build_handles_null_json(sample_session: dict, sample_summary: dict) -> None:
    sample_summary["tags_json"] = None
    sample_summary["files_touched_json"] = None
    fm = build_frontmatter(sample_session, sample_summary)
    assert fm["tags"] == []
    assert fm["files_touched"] == []


# --- serialize ---

def test_serialize_wraps_with_dashes() -> None:
    out = serialize({"a": "b"})
    assert out.startswith("---\n")
    assert out.endswith("---\n")


def test_serialize_list_inline() -> None:
    out = serialize({"tags": ["x", "y"]})
    assert "tags: [x, y]" in out


def test_serialize_empty_list() -> None:
    out = serialize({"tags": []})
    assert "tags: []" in out


def test_serialize_quotes_when_unsafe() -> None:
    out = serialize({"intent": "hello, world!"})
    assert '"hello, world!"' in out


def test_serialize_iso_timestamp_unquoted() -> None:
    out = serialize({"summarized_at": "2026-04-25T14:30:00Z"})
    assert "summarized_at: 2026-04-25T14:30:00Z" in out


def test_serialize_project_slug_quoted_due_to_leading_dash() -> None:
    out = serialize({"project": "-Users-cjons-tms-stt"})
    assert '"-Users-cjons-tms-stt"' in out


def test_serialize_null_value() -> None:
    out = serialize({"branch": None})
    assert "branch: null" in out


def test_serialize_int_value() -> None:
    out = serialize({"quality_score": 8})
    assert "quality_score: 8" in out
