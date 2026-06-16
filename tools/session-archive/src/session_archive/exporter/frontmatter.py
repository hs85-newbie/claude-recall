"""Obsidian frontmatter 빌더 (11필드 + lang 자동감지).

설계: docs/E-export-obsidian-design.md §4
"""
from __future__ import annotations

import json
import re
import sqlite3
from typing import Any

from ..machine import machine_id


SUMMARY_LEVEL = "L2"
KIND = "session"

_KO_SYL = (0xAC00, 0xD7A3)
_HIRAGANA = (0x3040, 0x309F)
_KATAKANA = (0x30A0, 0x30FF)
_CJK_UNIFIED = (0x4E00, 0x9FFF)


def detect_lang(text: str) -> str:
    """한글/CJK/카나 비율로 lang 감지. ko/ja/zh/en/und."""
    if not text or len(text.strip()) < 5:
        return "und"
    ko = kana = zh = total = 0
    for ch in text:
        code = ord(ch)
        if _KO_SYL[0] <= code <= _KO_SYL[1]:
            ko += 1
        elif _HIRAGANA[0] <= code <= _HIRAGANA[1] or _KATAKANA[0] <= code <= _KATAKANA[1]:
            kana += 1
        elif _CJK_UNIFIED[0] <= code <= _CJK_UNIFIED[1]:
            zh += 1
        if not ch.isspace():
            total += 1
    if total == 0:
        return "und"
    ko_r, kana_r, zh_r = ko / total, kana / total, zh / total
    if ko_r >= 0.30:
        return "ko"
    if kana_r >= 0.10:
        return "ja"
    if zh_r >= 0.30 and ko_r < 0.10:
        return "zh"
    return "en"


def normalize_tag(tag: str) -> str:
    """소문자 + 공백→하이픈 + 영숫자/하이픈/한글만 유지."""
    t = tag.lower().strip().replace(" ", "-")
    return re.sub(r"[^a-z0-9가-힣\-]", "", t)


def normalize_tags(tags: list[str]) -> list[str]:
    """중복·1자 미만 제거, 입력 순서 유지."""
    seen: set[str] = set()
    out: list[str] = []
    for t in tags:
        n = normalize_tag(t)
        if n and n not in seen:
            seen.add(n)
            out.append(n)
    return out


def _row_get(row: sqlite3.Row | dict, key: str, default: Any = None) -> Any:
    """sqlite3.Row와 dict 모두 지원."""
    try:
        v = row[key]
    except (KeyError, IndexError):
        return default
    return v if v is not None else default


def build_frontmatter(
    session_row: sqlite3.Row | dict,
    summary_row: sqlite3.Row | dict,
) -> dict[str, Any]:
    """sessions + session_summaries → frontmatter dict (11필드)."""
    intent = _row_get(summary_row, "intent", "")
    outcome = _row_get(summary_row, "outcome", "")
    tags_raw = json.loads(_row_get(summary_row, "tags_json", "[]") or "[]")
    files_raw = json.loads(_row_get(summary_row, "files_touched_json", "[]") or "[]")
    return {
        "session_id": _row_get(session_row, "session_id"),
        # WHY: 크로스머신 회상 라벨 — search_vault가 어느 머신 export인지 표기(필터 아닌 라벨)
        "machine": machine_id(),
        "project": _row_get(session_row, "project_slug"),
        "branch": _row_get(session_row, "git_branch"),
        "summarized_at": _row_get(summary_row, "summarized_at"),
        "model": _row_get(summary_row, "model"),
        "quality_score": _row_get(summary_row, "quality_score"),
        "summary_level": SUMMARY_LEVEL,
        "kind": KIND,
        "lang": detect_lang(f"{intent} {outcome}"),
        "tags": normalize_tags(tags_raw),
        "files_touched": files_raw,
    }


_SAFE_SCALAR = re.compile(r"^[a-zA-Z0-9가-힣_./:+\-]+$")


def _yaml_scalar(v: Any) -> str:
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    s = str(v)
    if not s:
        return '""'
    # WHY: 시작이 '-'면 YAML 리스트 아이템으로 오인되므로 quote 필요
    if _SAFE_SCALAR.match(s) and not s.startswith("-"):
        return s
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def serialize(fm: dict[str, Any]) -> str:
    """frontmatter dict → YAML-like 블록 (---로 감쌈)."""
    lines = ["---"]
    for k, v in fm.items():
        if isinstance(v, list):
            if not v:
                lines.append(f"{k}: []")
            else:
                inner = ", ".join(_yaml_scalar(item) for item in v)
                lines.append(f"{k}: [{inner}]")
        else:
            lines.append(f"{k}: {_yaml_scalar(v)}")
    lines.append("---")
    return "\n".join(lines) + "\n"
