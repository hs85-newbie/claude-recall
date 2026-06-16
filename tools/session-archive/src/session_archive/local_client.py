"""LM Studio OpenAI 호환 API 래퍼 (Gemma4 MoE 로컬 호출).

참고: ~/gemma4-bench (MEMORY: project_gemma4_bench.md)
- 기본 URL: http://localhost:1234/v1
- 모델: gemma-4-26b-a4b-it
- 호출 포맷: POST /chat/completions (OpenAI 호환)
- 응답: choices[0].message.content + usage.{prompt_tokens, completion_tokens}
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass


DEFAULT_URL = "http://localhost:1234/v1"
DEFAULT_MODEL = "gemma-4-26b-a4b-it"
DEFAULT_TIMEOUT = 240.0  # M2 Air Metal GPU 기준 26B MoE prompt eval + 생성 여유
DEFAULT_MAX_TOKENS = 2048


@dataclass
class LocalCallResult:
    model: str
    text: str
    input_tokens: int
    output_tokens: int
    latency_ms: int
    error: str | None = None


def _endpoint() -> str:
    return os.environ.get("LM_STUDIO_URL", DEFAULT_URL).rstrip("/")


def _model() -> str:
    return os.environ.get("LOCAL_LLM_MODEL", DEFAULT_MODEL)


def ping(timeout: float = 2.0) -> bool:
    """LM Studio 헬스체크. /v1/models 조회 성공 시 True."""
    try:
        with urllib.request.urlopen(f"{_endpoint()}/models", timeout=timeout) as resp:
            return resp.status == 200
    except (urllib.error.URLError, TimeoutError, ConnectionError, OSError):
        return False


def call_local(
    system: str,
    user: str,
    *,
    model: str | None = None,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    timeout: float = DEFAULT_TIMEOUT,
    temperature: float = 0.2,
) -> LocalCallResult:
    """OpenAI 호환 chat completion 호출. 에러 시 error 필드로 보고."""
    model = model or _model()
    url = f"{_endpoint()}/chat/completions"
    payload = {
        "model": model,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    }
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    t0 = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
        latency_ms = int((time.monotonic() - t0) * 1000)
    except urllib.error.HTTPError as e:
        return LocalCallResult(
            model=model, text="", input_tokens=0, output_tokens=0,
            latency_ms=int((time.monotonic() - t0) * 1000),
            error=f"HTTPError {e.code}: {e.reason}",
        )
    except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
        return LocalCallResult(
            model=model, text="", input_tokens=0, output_tokens=0,
            latency_ms=int((time.monotonic() - t0) * 1000),
            error=f"{type(e).__name__}: {e}",
        )

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        return LocalCallResult(
            model=model, text="", input_tokens=0, output_tokens=0,
            latency_ms=latency_ms, error=f"invalid JSON response: {e}",
        )

    choices = data.get("choices") or []
    text = ""
    if choices:
        msg = choices[0].get("message") or {}
        text = msg.get("content") or ""

    usage = data.get("usage") or {}
    in_tok = int(usage.get("prompt_tokens") or 0)
    out_tok = int(usage.get("completion_tokens") or 0)

    return LocalCallResult(
        model=model,
        text=text,
        input_tokens=in_tok,
        output_tokens=out_tok,
        latency_ms=latency_ms,
        error=None if text else "empty_response",
    )
