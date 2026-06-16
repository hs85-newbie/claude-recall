"""exporter/body 단위 테스트."""
from __future__ import annotations

import json

import pytest

from session_archive.exporter.body import (
    USER_QUOTE_TRUNC,
    _section_backlinks,
    _section_commits,
    _section_decisions,
    _section_files,
    _section_intent,
    _section_intent_title,
    _section_outcome,
    _section_user_quotes,
    _truncate,
    _wikilink_slug,
    build_body,
)


# --- intent title (H1) ---

def test_intent_title_normal() -> None:
    assert _section_intent_title("OCR 정책 결정") == "# OCR 정책 결정"


def test_intent_title_empty() -> None:
    assert _section_intent_title("") == "# (no intent)"


def test_intent_title_takes_first_line() -> None:
    assert _section_intent_title("first line\nsecond") == "# first line"


# --- intent / outcome 섹션 ---

def test_intent_section_normal() -> None:
    out = _section_intent("OCR 정책")
    assert out.startswith("## Intent\n\n")
    assert "OCR 정책" in out


def test_intent_section_empty_uses_placeholder() -> None:
    out = _section_intent("")
    assert "_없음_" in out


def test_outcome_section_normal() -> None:
    out = _section_outcome("OCR 유지 결정")
    assert "## Outcome" in out
    assert "OCR 유지 결정" in out


def test_outcome_section_none() -> None:
    out = _section_outcome(None)
    assert "_없음_" in out


# --- decisions ---

def test_decisions_with_rationale() -> None:
    raw = json.dumps([{"decision": "OCR 유지", "rationale": "STT 품질 향상"}])
    out = _section_decisions(raw)
    assert "1. **OCR 유지**" in out
    assert "근거: STT 품질 향상" in out


def test_decisions_without_rationale() -> None:
    raw = json.dumps([{"decision": "결정", "rationale": ""}])
    out = _section_decisions(raw)
    assert "1. **결정**" in out
    assert "근거" not in out


def test_decisions_empty() -> None:
    assert "_없음_" in _section_decisions("[]")


def test_decisions_max_5_truncation() -> None:
    raw = json.dumps([{"decision": f"d{i}", "rationale": ""} for i in range(7)])
    out = _section_decisions(raw)
    assert "**d4**" in out
    assert "**d5**" not in out
    assert "**d6**" not in out


# --- files ---

def test_files_normal() -> None:
    out = _section_files(json.dumps(["src/a.ts", "src/b.ts"]))
    assert "- src/a.ts" in out
    assert "- src/b.ts" in out


def test_files_empty() -> None:
    assert "_없음_" in _section_files("[]")


# --- commits ---

def test_commits_truncates_to_7() -> None:
    out = _section_commits(json.dumps(["9cf42ec239672937e2b25766d02a99", "abc12345"]))
    assert "- 9cf42ec" in out
    assert "9cf42ec239" not in out  # 7자 초과는 출력 안 됨
    assert "- abc1234" in out


def test_commits_empty() -> None:
    assert "_없음_" in _section_commits("[]")


# --- truncate ---

def test_truncate_under_limit() -> None:
    assert _truncate("hello", 10) == "hello"


def test_truncate_over_limit() -> None:
    out = _truncate("a" * 600, 500)
    assert out.endswith("…")
    assert len(out) == 501  # 500 + …


# --- user quotes ---

def test_user_quotes_callout_format() -> None:
    out = _section_user_quotes(["첫 번째 질문", "두 번째 질문"])
    assert "> [!quote] 1" in out
    assert "> 첫 번째 질문" in out
    assert "> [!quote] 2" in out
    assert "> 두 번째 질문" in out


def test_user_quotes_empty() -> None:
    assert "_없음_" in _section_user_quotes([])


def test_user_quotes_truncates_long() -> None:
    long_q = "가" * 600
    out = _section_user_quotes([long_q])
    # 인용 라인 길이 = USER_QUOTE_TRUNC + … (callout > 접두 제외)
    assert "…" in out


def test_user_quotes_multiline() -> None:
    out = _section_user_quotes(["line1\nline2"])
    assert "> line1" in out
    assert "> line2" in out


# --- wikilink slug ---

@pytest.mark.parametrize(
    "src,expected",
    [
        ("src/a.ts", "src-a"),
        ("apps/web/lib/foo.ts", "apps-web-lib-foo"),
        ("README.md", "README"),
        ("path with space/file.txt", "path_with_space-file"),
        ("hangul/한글.md", "hangul-한글"),
        ("no-extension", "no-extension"),
    ],
)
def test_wikilink_slug(src: str, expected: str) -> None:
    assert _wikilink_slug(src) == expected


# --- backlinks ---

def test_backlinks_with_project_and_files() -> None:
    out = _section_backlinks("-Users-cjons-tms-stt", json.dumps(["src/a.ts"]))
    assert "[[projects/-Users-cjons-tms-stt]]" in out
    assert "[[files/src-a]]" in out


def test_backlinks_only_project() -> None:
    out = _section_backlinks("-Users-cjons-tms-stt", "[]")
    assert "Project:" in out
    assert "File:" not in out


def test_backlinks_empty() -> None:
    out = _section_backlinks(None, "[]")
    assert "_없음_" in out


# --- build_body 통합 ---

def test_build_body_contains_all_7_sections() -> None:
    session = {
        "session_id": "sid",
        "project_slug": "-Users-cjons-tms-stt",
        "git_branch": "dev",
    }
    summary = {
        "intent": "tms-stt OCR 정책",
        "outcome": "OCR 유지",
        "decisions_json": json.dumps([{"decision": "유지", "rationale": "품질"}]),
        "files_touched_json": json.dumps(["src/a.ts"]),
        "related_commits_json": json.dumps(["9cf42ec239"]),
    }
    body = build_body(session, summary, ["원문 1"])
    # H1 + 7 H2 섹션
    assert body.startswith("# tms-stt OCR 정책")
    for h in ["## Intent", "## Outcome", "## Decisions", "## Files Touched",
              "## Related Commits", "## User 원문", "## Backlinks"]:
        assert h in body
