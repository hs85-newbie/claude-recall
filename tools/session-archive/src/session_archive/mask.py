"""시크릿 마스킹 (ADR-001 §2.7).

원본 L0 JSONL은 그대로 남고, L1 적재 시점에만 치환한다.
카테고리별 히트 수는 호출자가 mask_stats 테이블에 집계한다.
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from typing import Iterable

# NOTE: env-var 라인 패턴은 다른 것들보다 먼저 적용되어야 함.
#       (key/value 전체를 잡은 뒤 내부에서 value만 치환하므로,
#        value에 들어있던 sk-... 등이 이미 가려진 상태가 된다.)
_RULES: list[tuple[str, re.Pattern[str], str]] = [
    (
        "env_var",
        re.compile(
            r"""(?ix)
            (?P<key>
                (?:[A-Za-z_][A-Za-z0-9_\-]*?)?  # 선택적 접두사 (DB_, my_ 등) — 최소 매칭
                (?:password|passwd|secret|api[_-]?key|token|access[_-]?key)
                [A-Za-z0-9_]*                    # 접미사
            )
            (?P<sep>
                ["']?\s*[:=]\s*["']?             # JSON/YAML/ENV/쉘 모두 대응
            )
            (?P<val>[^\s"',#}]+)                 # 공백·따옴표·주석 앞에서 멈춤
            """
        ),
        r"\g<key>\g<sep>[REDACTED:ENV]",
    ),
    (
        "anthropic_key",
        re.compile(r"sk-ant-[A-Za-z0-9_\-]{20,}"),
        "[REDACTED:API_KEY]",
    ),
    (
        "openai_key",
        re.compile(r"sk-(?!ant-)[A-Za-z0-9_\-]{20,}"),
        "[REDACTED:API_KEY]",
    ),
    (
        "github_pat_new",
        re.compile(r"github_pat_[A-Za-z0-9_]{82}"),
        "[REDACTED:GH_TOKEN]",
    ),
    (
        "github_token",
        re.compile(r"gh[pousr]_[A-Za-z0-9]{36,255}"),
        "[REDACTED:GH_TOKEN]",
    ),
    (
        "aws_access_key",
        re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
        "[REDACTED:AWS_KEY]",
    ),
    (
        "jwt",
        re.compile(r"\beyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\b"),
        "[REDACTED:JWT]",
    ),
]

# ADR §2.7: email은 기본 OFF. 옵트인 시에만 켠다.
_EMAIL_RULE = (
    "email",
    re.compile(r"[\w.+\-]+@[\w\-]+\.[\w.\-]+"),
    "[REDACTED:EMAIL]",
)


@dataclass
class MaskResult:
    text: str
    hits: Counter  # category -> count

    @property
    def masked(self) -> bool:
        return sum(self.hits.values()) > 0


def mask_text(text: str, *, mask_email: bool = False) -> MaskResult:
    """주어진 텍스트에 마스킹 규칙을 순서대로 적용한다."""
    if not text:
        return MaskResult(text=text, hits=Counter())

    hits: Counter = Counter()
    rules: Iterable[tuple[str, re.Pattern[str], str]] = _RULES
    if mask_email:
        rules = list(_RULES) + [_EMAIL_RULE]

    result = text
    for name, pattern, replacement in rules:
        def _sub(_m: re.Match[str], _name: str = name) -> str:
            hits[_name] += 1
            return pattern.sub(replacement, _m.group(0))

        # count+substitute in one pass
        new_result, n = pattern.subn(replacement, result)
        if n:
            hits[name] += n
        result = new_result

    return MaskResult(text=result, hits=hits)
