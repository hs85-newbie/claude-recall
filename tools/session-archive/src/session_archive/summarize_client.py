"""Anthropic SDK 래퍼: 재시도, JSON 파싱, 예산 트래킹.

모델 전략 (설계 노트):
- 기본: Haiku 4.5
- quality_score < 5 OR JSON 파싱 실패 → Sonnet 4.6 재시도 (세션당 최대 2회)
- 5xx/timeout: 지수 백오프 1s→2s→4s, 3회 후 포기

예산:
- 일일 $10 상한. summarize_budget 테이블에 누적.
"""
from __future__ import annotations

import json
import re
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import anthropic


MODEL_DEFAULT = "claude-haiku-4-5-20251001"
MODEL_RETRY = "claude-sonnet-4-6"
MODEL_OPUS = "claude-opus-4-7"

# 1M 토큰당 USD. SDK usage는 토큰 단위로 반환.
PRICING = {
    MODEL_DEFAULT: {"input": 1.00, "output": 5.00},
    MODEL_RETRY: {"input": 3.00, "output": 15.00},
    MODEL_OPUS: {"input": 15.00, "output": 75.00},
}

# WHY: 2048은 산문 리드(머리말) 후 JSON이 잘려 json_parse_failed를 유발 → 4096으로 상향
MAX_OUTPUT_TOKENS = 4096
DAILY_BUDGET_USD = 10.0
HTTP_RETRY_MAX = 3
HTTP_RETRY_BASE_SEC = 1.0

QUALITY_THRESHOLD = 5  # 이 값 미만이면 Sonnet 재시도

# WHY: 구조화 출력(structured outputs)으로 JSON 강제 — 모델이 산문/원문 echo로
#      이탈해 json_parse_failed가 나던 결함 차단. Haiku 4.5/Sonnet 4.6/Opus 4.x 지원.
#      구조화 출력 제약: 모든 object에 additionalProperties:false, min/max 제약 불가.
SUMMARY_SCHEMA = {
    "type": "object",
    "properties": {
        "intent": {"type": "string"},
        "outcome": {"type": "string"},
        "decisions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "decision": {"type": "string"},
                    "rationale": {"type": "string"},
                },
                "required": ["decision", "rationale"],
                "additionalProperties": False,
            },
        },
        "tags": {"type": "array", "items": {"type": "string"}},
        "files_touched": {"type": "array", "items": {"type": "string"}},
        "quality_score": {"type": "integer"},
    },
    "required": [
        "intent",
        "outcome",
        "decisions",
        "tags",
        "files_touched",
        "quality_score",
    ],
    "additionalProperties": False,
}

# Opus 쿼터 (§6-bis)
DAILY_OPUS_CALLS_MAX = 3
OPUS_MIN_BUDGET_REMAINING_USD = 1.0


class BudgetExceeded(Exception):
    pass


@dataclass
class ModelCall:
    model: str
    input_tokens: int
    output_tokens: int
    cost_usd: float
    parsed: dict | None
    raw_text: str
    error: str | None = None
    latency_ms: int = 0


def _load_env_file(path: Path) -> None:
    """~/.env에서 KEY=VAL 로드. 이미 환경에 있으면 덮어쓰지 않음."""
    import os

    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        k = k.strip()
        v = v.strip().strip("'").strip('"')
        if k and k not in os.environ:
            os.environ[k] = v


def get_client() -> anthropic.Anthropic:
    _load_env_file(Path.home() / ".env")
    # WHY: 레포 루트 .env(gitignore됨)도 로드 — 키를 프로젝트에 두는 경우 지원 (이식성)
    _load_env_file(Path(__file__).resolve().parents[4] / ".env")
    return anthropic.Anthropic()


def _compute_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    p = PRICING.get(model) or PRICING[MODEL_DEFAULT]
    return (input_tokens * p["input"] + output_tokens * p["output"]) / 1_000_000


def _today_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _get_today_spend(conn: sqlite3.Connection) -> float:
    row = conn.execute(
        "SELECT cost_usd FROM summarize_budget WHERE day_utc = ?", (_today_utc(),)
    ).fetchone()
    return float(row["cost_usd"]) if row else 0.0


def _record_spend(
    conn: sqlite3.Connection,
    input_tokens: int,
    output_tokens: int,
    cost_usd: float,
    *,
    is_opus: bool = False,
) -> None:
    conn.execute(
        """
        INSERT INTO summarize_budget (day_utc, input_tokens, output_tokens, cost_usd, call_count, opus_calls)
        VALUES (?, ?, ?, ?, 1, ?)
        ON CONFLICT(day_utc) DO UPDATE SET
            input_tokens = input_tokens + excluded.input_tokens,
            output_tokens = output_tokens + excluded.output_tokens,
            cost_usd = cost_usd + excluded.cost_usd,
            call_count = call_count + 1,
            opus_calls = opus_calls + excluded.opus_calls
        """,
        (_today_utc(), input_tokens, output_tokens, cost_usd, 1 if is_opus else 0),
    )


def get_opus_calls_today(conn: sqlite3.Connection) -> int:
    row = conn.execute(
        "SELECT opus_calls FROM summarize_budget WHERE day_utc = ?", (_today_utc(),)
    ).fetchone()
    return int(row["opus_calls"]) if row else 0


def check_opus_quota(conn: sqlite3.Connection) -> tuple[bool, str]:
    """Opus 호출 가능 여부. (ok, reason_if_not)."""
    calls = get_opus_calls_today(conn)
    if calls >= DAILY_OPUS_CALLS_MAX:
        return False, f"daily_opus_calls_exhausted ({calls}/{DAILY_OPUS_CALLS_MAX})"
    spent = _get_today_spend(conn)
    remaining = DAILY_BUDGET_USD - spent
    if remaining < OPUS_MIN_BUDGET_REMAINING_USD:
        return False, f"budget_remaining_too_low (${remaining:.2f} < ${OPUS_MIN_BUDGET_REMAINING_USD:.2f})"
    return True, ""


def _check_budget(conn: sqlite3.Connection, est_cost: float) -> None:
    spent = _get_today_spend(conn)
    if spent + est_cost > DAILY_BUDGET_USD:
        raise BudgetExceeded(
            f"daily budget ${DAILY_BUDGET_USD:.2f} exceeded: spent=${spent:.4f} est+=${est_cost:.4f}"
        )


_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


def _parse_json_response(text: str) -> dict | None:
    """모델 응답에서 JSON 객체 추출. 코드펜스/접두사 관용."""
    stripped = _FENCE_RE.sub("", text).strip()
    try:
        obj = json.loads(stripped)
    except json.JSONDecodeError:
        # 첫 { ... 마지막 } 사이만 재시도
        start = stripped.find("{")
        end = stripped.rfind("}")
        if start < 0 or end <= start:
            return None
        try:
            obj = json.loads(stripped[start : end + 1])
        except json.JSONDecodeError:
            return None
    return obj if isinstance(obj, dict) else None


def _extract_text(resp) -> str:
    parts: list[str] = []
    for blk in getattr(resp, "content", []) or []:
        if getattr(blk, "type", None) == "text":
            parts.append(getattr(blk, "text", "") or "")
    return "\n".join(parts)


def _call_with_backoff(client: anthropic.Anthropic, **kwargs):
    last_exc: Exception | None = None
    for attempt in range(HTTP_RETRY_MAX):
        try:
            return client.messages.create(**kwargs)
        except (
            anthropic.APIConnectionError,
            anthropic.APITimeoutError,
            anthropic.RateLimitError,
        ) as e:
            last_exc = e
        except anthropic.APIStatusError as e:
            # 5xx만 재시도
            if getattr(e, "status_code", 0) < 500:
                raise
            last_exc = e
        time.sleep(HTTP_RETRY_BASE_SEC * (2**attempt))
    raise last_exc if last_exc else RuntimeError("retry loop ended without exception")


def call_model(
    client: anthropic.Anthropic,
    conn: sqlite3.Connection,
    *,
    model: str,
    system: str,
    user: str,
    est_input_tokens: int,
) -> ModelCall:
    """단일 모델 호출 + 예산 체크 + JSON 파싱. 에러 시 error 필드로 보고."""
    # 사전 예산 가드: 출력 토큰은 상한값 기준 상측 추정
    est_cost = _compute_cost(model, est_input_tokens, MAX_OUTPUT_TOKENS)
    _check_budget(conn, est_cost)

    t0 = time.monotonic()
    try:
        resp = _call_with_backoff(
            client,
            model=model,
            max_tokens=MAX_OUTPUT_TOKENS,
            system=system,
            messages=[{"role": "user", "content": user}],
            output_config={"format": {"type": "json_schema", "schema": SUMMARY_SCHEMA}},
        )
    except Exception as e:
        return ModelCall(
            model=model,
            input_tokens=0,
            output_tokens=0,
            cost_usd=0.0,
            parsed=None,
            raw_text="",
            error=f"{type(e).__name__}: {e}",
            latency_ms=int((time.monotonic() - t0) * 1000),
        )
    latency_ms = int((time.monotonic() - t0) * 1000)

    usage = getattr(resp, "usage", None)
    in_tok = int(getattr(usage, "input_tokens", 0) or 0)
    out_tok = int(getattr(usage, "output_tokens", 0) or 0)
    cost = _compute_cost(model, in_tok, out_tok)
    _record_spend(conn, in_tok, out_tok, cost, is_opus=(model == MODEL_OPUS))

    text = _extract_text(resp)
    parsed = _parse_json_response(text)
    err = None if parsed is not None else "json_parse_failed"

    return ModelCall(
        model=model,
        input_tokens=in_tok,
        output_tokens=out_tok,
        cost_usd=cost,
        parsed=parsed,
        raw_text=text,
        error=err,
        latency_ms=latency_ms,
    )


def should_retry(result: ModelCall) -> bool:
    """Haiku 결과가 Sonnet 재시도 대상인지."""
    if result.error == "json_parse_failed":
        return True
    if result.parsed is None:
        return True
    q = result.parsed.get("quality_score")
    try:
        return int(q) < QUALITY_THRESHOLD
    except (TypeError, ValueError):
        return True
