"""scorer 단위 테스트 — deterministic 기반 4축 점수."""
from session_archive.scorer import (
    COMPOSITE_GATE,
    Scores,
    decide_winner,
    evaluate,
    needs_opus_escalation,
    score_grounding,
    score_richness,
    score_schema,
    score_self_quality,
)


GOOD_OBJ = {
    "intent": "세션 로그 아카이브 L2 요약 파이프라인 구현",
    "outcome": "Gemma4+Haiku 비교 모드 추가, CLI 연결",
    "decisions": [
        {"decision": "4축 점수 규칙", "rationale": "LLM 자체평가 대신 재현 가능한 코드로"},
        {"decision": "Opus 쿼터 3회", "rationale": "비용 보호"},
    ],
    "tags": ["session-archive", "L2", "compare", "opus", "quota"],
    "files_touched": ["/Users/you/my-claude-global/tools/session-archive/src/session_archive/scorer.py"],
    "quality_score": 8,
}


def test_schema_full_marks():
    score, issues = score_schema(GOOD_OBJ)
    assert score == 6
    assert issues == []


def test_schema_missing_intent():
    obj = {**GOOD_OBJ, "intent": ""}
    score, issues = score_schema(obj)
    assert score == 5
    assert "intent_missing" in issues


def test_schema_decisions_wrong_shape():
    obj = {**GOOD_OBJ, "decisions": ["한 줄짜리 스트링"]}
    score, issues = score_schema(obj)
    assert score == 5
    assert "decisions_shape" in issues


def test_schema_not_dict():
    s, i = score_schema("not a dict")
    assert s == 0
    assert i == ["not_dict"]


def test_richness_good():
    assert score_richness(GOOD_OBJ) == 6  # intent 길이, decisions>=2, tags 3~8, outcome


def test_richness_short_intent_no_outcome():
    obj = {"intent": "짧", "decisions": [], "tags": [], "outcome": None}
    assert score_richness(obj) == 0


def test_richness_tag_count_bands():
    # 1~2개: +1
    assert score_richness({"intent": "x" * 50, "decisions": [], "tags": ["a"], "outcome": None}) == 2  # intent+tags1


def test_grounding_files_match():
    obj = {
        "intent": "x" * 20,
        "files_touched": ["/a/b.py", "/a/c.py"],
        "tags": ["compare"],
        "decisions": [{"decision": "hello world", "rationale": "x"}],
    }
    corpus = "blah compare blah"
    real = {"/a/b.py", "/a/c.py"}
    score = score_grounding(obj, corpus=corpus, real_paths=real)
    # files 2/2 → +3, tags 1/1 → +2, decisions decision>=5자 → +1 = 6
    assert score == 6


def test_grounding_files_partial():
    obj = {"files_touched": ["/a/b.py", "/z.py"], "tags": [], "decisions": []}
    score = score_grounding(obj, corpus="", real_paths={"/a/b.py"})
    # 0.5 → +1
    assert score == 1


def test_grounding_no_files_no_penalty():
    """파일 언급 없는 세션은 penalize 안 함 (real_ratio=1.0)."""
    obj = {"files_touched": [], "tags": [], "decisions": []}
    score = score_grounding(obj, corpus="", real_paths=set())
    assert score == 3  # files +3 (no items = clean)


def test_self_quality_bands():
    assert score_self_quality({"quality_score": 9}) == 2
    assert score_self_quality({"quality_score": 7}) == 1
    assert score_self_quality({"quality_score": 3}) == 0
    assert score_self_quality({"quality_score": "abc"}) == 0
    assert score_self_quality({}) == 0


def test_evaluate_composite():
    real = {GOOD_OBJ["files_touched"][0]}
    corpus = " ".join(GOOD_OBJ["tags"]) + " session-archive"
    s = evaluate(GOOD_OBJ, corpus=corpus, real_paths=real)
    assert s.schema == 6
    assert s.composite == s.schema * 3 + s.richness * 2 + s.grounding * 2 + s.self_quality


def test_decide_winner_both_fail():
    left = Scores(2, 0, 0, 0, 6, [])
    right = Scores(3, 0, 0, 0, 9, [])
    r = decide_winner("gemma", left, "haiku", right, tie_preferred="gemma")
    assert r.winner is None
    assert r.needs_sonnet is True


def test_decide_winner_only_one_passes():
    left = Scores(6, 3, 3, 1, 6 * 3 + 3 * 2 + 3 * 2 + 1, [])
    right = Scores(5, 5, 5, 2, 5 * 3 + 5 * 2 + 5 * 2 + 2, [])
    r = decide_winner("gemma", left, "haiku", right, tie_preferred="gemma")
    assert r.winner == "gemma"
    assert r.reason == "gemma_only_passed"


def test_decide_winner_composite_gap():
    left = Scores(6, 5, 5, 2, 6 * 3 + 5 * 2 + 5 * 2 + 2, [])  # 40
    right = Scores(6, 3, 3, 1, 6 * 3 + 3 * 2 + 3 * 2 + 1, [])  # 31
    r = decide_winner("gemma", left, "haiku", right, tie_preferred="gemma")
    assert r.winner == "gemma"
    assert r.reason == "composite_gap"


def test_decide_winner_tie_prefers_gemma():
    left = Scores(6, 3, 3, 1, 31, [])
    right = Scores(6, 3, 3, 1, 31, [])  # identical
    r = decide_winner("gemma", left, "haiku", right, tie_preferred="gemma")
    assert r.winner == "gemma"
    assert "tie" in r.reason


def test_decide_winner_grounding_tiebreak():
    # composite 동률이지만 grounding 차이
    left = Scores(6, 4, 5, 0, 6 * 3 + 4 * 2 + 5 * 2 + 0, [])  # 36, grounding=5
    right = Scores(6, 5, 4, 0, 6 * 3 + 5 * 2 + 4 * 2 + 0, [])  # 36, grounding=4
    assert left.composite == right.composite
    r = decide_winner("gemma", left, "haiku", right, tie_preferred="haiku")
    assert r.winner == "gemma"
    assert r.reason == "grounding_tiebreak"


def test_needs_opus_schema_failed():
    s = Scores(5, 6, 6, 2, 35, [])
    needed, reason = needs_opus_escalation(s)
    assert needed
    assert reason == "sonnet_schema_failed"


def test_needs_opus_composite_low():
    s = Scores(6, 0, 0, 0, 18, [])  # composite=18 >= 15, but... let's force
    s = Scores(6, 0, 0, 0, 14, [])
    needed, reason = needs_opus_escalation(s)
    assert needed
    assert reason == "sonnet_composite_low"


def test_needs_opus_grounding_low():
    s = Scores(6, 5, 2, 2, 34, [])
    needed, reason = needs_opus_escalation(s)
    assert needed
    assert reason == "sonnet_grounding_low"


def test_needs_opus_none():
    s = Scores(6, 5, 5, 2, 40, [])
    needed, reason = needs_opus_escalation(s)
    assert not needed
    assert reason == ""
