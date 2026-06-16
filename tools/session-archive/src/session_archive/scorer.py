"""L2 요약 결과 4축 규칙 기반 점수 계산 + winner/escalation 판정.

설계: docs/reports/2026-04-17-session-archive-L2-summary-품질비교기준.md

축:
- A schema_score   0~6  (필수 게이트: 6점 미달 시 자동 탈락)
- B richness_score 0~6
- C grounding_score 0~6
- D self_quality   0~2  (모델이 매긴 quality_score)

composite = schema*3 + richness*2 + grounding*2 + self_quality*1
"""
from __future__ import annotations

import re
from dataclasses import dataclass


SCHEMA_MAX = 6
COMPOSITE_GATE = SCHEMA_MAX  # schema_score 만점이어야 통과
COMPOSITE_MARGIN = 3  # 두 후보 근소 차이 임계
OPUS_COMPOSITE_THRESHOLD = 15  # Sonnet composite이 이 값 미만이면 Opus 승급
OPUS_GROUNDING_THRESHOLD = 2


@dataclass
class Scores:
    schema: int
    richness: int
    grounding: int
    self_quality: int
    composite: int
    issues: list[str]


def _is_str(x) -> bool:
    return isinstance(x, str) and x.strip() != ""


def _is_str_list(x) -> bool:
    return isinstance(x, list) and all(isinstance(v, str) for v in x)


def score_schema(obj: dict | None) -> tuple[int, list[str]]:
    """필수 필드/타입 만점 기준 0~6. 이슈 문자열 리스트 동반."""
    if not isinstance(obj, dict):
        return 0, ["not_dict"]
    issues: list[str] = []
    score = 1  # dict 파싱 성공

    if _is_str(obj.get("intent")):
        score += 1
    else:
        issues.append("intent_missing")

    if "outcome" in obj:
        score += 1
    else:
        issues.append("outcome_missing")

    decisions = obj.get("decisions")
    if isinstance(decisions, list) and all(
        isinstance(d, dict) and _is_str(d.get("decision")) and _is_str(d.get("rationale"))
        for d in decisions
    ):
        score += 1
    else:
        issues.append("decisions_shape")

    if _is_str_list(obj.get("tags") or []):
        score += 1
    else:
        issues.append("tags_shape")

    if _is_str_list(obj.get("files_touched") or []):
        score += 1
    else:
        issues.append("files_touched_shape")

    return score, issues


def score_richness(obj: dict) -> int:
    score = 0
    intent = obj.get("intent") or ""
    if 10 <= len(intent) <= 200:
        score += 1
    decisions = obj.get("decisions") or []
    if len(decisions) >= 1:
        score += 1
    if len(decisions) >= 2:
        score += 1
    tags = obj.get("tags") or []
    n = len(tags)
    if 3 <= n <= 8:
        score += 2
    elif 1 <= n <= 12:
        score += 1
    if _is_str(obj.get("outcome")):
        score += 1
    return min(score, 6)


def _count_match_ratio(items: list[str], corpus: str) -> float:
    if not items:
        return 0.0
    corpus_lower = corpus.lower()
    hits = sum(1 for s in items if isinstance(s, str) and s.lower() in corpus_lower)
    return hits / len(items)


def score_grounding(obj: dict, *, corpus: str, real_paths: set[str]) -> int:
    score = 0

    files = obj.get("files_touched") or []
    if files:
        real_ratio = sum(1 for p in files if p in real_paths) / len(files)
    else:
        # 파일 언급 없음은 penalize 안 함 (세션에 파일 수정 없을 수 있음)
        real_ratio = 1.0
    if real_ratio >= 0.7:
        score += 3
    elif real_ratio >= 0.3:
        score += 1

    tags = obj.get("tags") or []
    tag_ratio = _count_match_ratio(tags, corpus) if tags else 0.0
    if tag_ratio >= 0.5:
        score += 2
    elif tag_ratio >= 0.2:
        score += 1

    decisions = obj.get("decisions") or []
    if decisions and all(
        isinstance(d, dict) and _is_str(d.get("decision")) and len(d["decision"]) >= 5
        for d in decisions
    ):
        score += 1

    return min(score, 6)


def score_self_quality(obj: dict) -> int:
    q = obj.get("quality_score")
    try:
        qi = int(q)
    except (TypeError, ValueError):
        return 0
    if qi >= 8:
        return 2
    if qi >= 5:
        return 1
    return 0


def evaluate(obj: dict | None, *, corpus: str, real_paths: set[str]) -> Scores:
    schema, issues = score_schema(obj)
    if not isinstance(obj, dict):
        return Scores(schema, 0, 0, 0, schema * 3, issues)

    richness = score_richness(obj)
    grounding = score_grounding(obj, corpus=corpus, real_paths=real_paths)
    self_q = score_self_quality(obj)
    composite = schema * 3 + richness * 2 + grounding * 2 + self_q * 1
    return Scores(schema, richness, grounding, self_q, composite, issues)


@dataclass
class ComparisonResult:
    winner: str | None  # model name or None if both failed
    reason: str  # "gemma_only_passed" / "haiku_only_passed" / "composite_gap" / "grounding_tiebreak" / "tie_gemma_wins" / "both_failed"
    needs_sonnet: bool


def decide_winner(
    left_model: str,
    left: Scores,
    right_model: str,
    right: Scores,
    *,
    tie_preferred: str,
) -> ComparisonResult:
    """left=Gemma, right=Haiku 관례. tie_preferred는 동점/근소 시 우선 모델."""
    left_pass = left.schema >= COMPOSITE_GATE
    right_pass = right.schema >= COMPOSITE_GATE

    if not left_pass and not right_pass:
        return ComparisonResult(winner=None, reason="both_failed", needs_sonnet=True)
    if left_pass and not right_pass:
        return ComparisonResult(winner=left_model, reason="gemma_only_passed", needs_sonnet=False)
    if right_pass and not left_pass:
        return ComparisonResult(winner=right_model, reason="haiku_only_passed", needs_sonnet=False)

    # 둘 다 schema 통과
    gap = left.composite - right.composite
    if abs(gap) >= COMPOSITE_MARGIN:
        winner = left_model if gap > 0 else right_model
        return ComparisonResult(winner=winner, reason="composite_gap", needs_sonnet=False)

    # 근소 → grounding 기준
    if left.grounding > right.grounding:
        return ComparisonResult(winner=left_model, reason="grounding_tiebreak", needs_sonnet=False)
    if right.grounding > left.grounding:
        return ComparisonResult(winner=right_model, reason="grounding_tiebreak", needs_sonnet=False)

    # 동점
    return ComparisonResult(
        winner=tie_preferred,
        reason=f"tie_{tie_preferred}_wins",
        needs_sonnet=False,
    )


def needs_opus_escalation(sonnet_scores: Scores) -> tuple[bool, str]:
    """Sonnet 결과 기준 Opus 승급 필요 여부. (needed, reason)."""
    if sonnet_scores.schema < COMPOSITE_GATE:
        return True, "sonnet_schema_failed"
    if sonnet_scores.composite < OPUS_COMPOSITE_THRESHOLD:
        return True, "sonnet_composite_low"
    if sonnet_scores.grounding <= OPUS_GROUNDING_THRESHOLD:
        return True, "sonnet_grounding_low"
    return False, ""
