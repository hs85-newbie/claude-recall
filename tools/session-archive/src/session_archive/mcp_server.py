"""session-archive MCP 서버 — Claude Code가 과거 세션/결정/체크포인트를 자동 회상.

stdio 위 JSON-RPC 2.0(MCP stdio 전송: 줄 단위 newline-delimited 메시지)을
표준 라이브러리만으로 구현한다. 외부 MCP SDK 의존성 없음 — 복잡성·설치 비용 최소화.

노출 도구:
  - search_history     : 세션 이벤트 + 체크포인트 FTS 통합 검색
  - recall_decisions   : L2 요약에서 내려진 과거 결정 회상
  - recent_checkpoints : 최신 체크포인트("어디까지 했더라")

DB는 읽기 전용으로 연다. 파이프라인이 아직 안 돌아 DB가 없으면
도구 호출은 친화적 안내 메시지를 isError로 반환한다(서버는 죽지 않음).
"""
from __future__ import annotations

import json
import sqlite3
import sys
from typing import Any, Callable

from . import recall

PROTOCOL_VERSION = "2025-06-18"
SERVER_INFO = {"name": "session-archive-recall", "version": "0.1.0"}

# JSON-RPC 표준 에러 코드
_PARSE_ERROR = -32700
_INVALID_REQUEST = -32600
_METHOD_NOT_FOUND = -32601
_INTERNAL_ERROR = -32603


TOOLS: list[dict] = [
    {
        "name": "search_history",
        "description": (
            "과거 Claude Code 세션 메시지와 gstack 체크포인트를 전문(FTS) 검색한다. "
            "'예전에 이거 어떻게 했더라', '저번에 무슨 에러였지' 같은 회상에 사용."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "검색어(한글/영문)"},
                "project": {"type": "string", "description": "project_slug 부분 일치 필터(선택)"},
                "since": {"type": "string", "description": "기간 필터: 7d / 24h / 30m (선택)"},
                "limit": {"type": "integer", "description": "각 종류별 최대 결과 수(기본 20)"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "recall_decisions",
        "description": (
            "과거 세션에서 내려진 설계/구현 결정과 그 근거를 회상한다. "
            "'이거 왜 이렇게 정했더라', '예전 결정 찾아줘'에 사용. query 없으면 최신 결정 전체."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "결정/근거 부분 문자열 필터(선택)"},
                "project": {"type": "string", "description": "project_slug 부분 일치 필터(선택)"},
                "limit": {"type": "integer", "description": "최대 결과 수(기본 20)"},
            },
        },
    },
    {
        "name": "recent_checkpoints",
        "description": (
            "최신 작업 체크포인트(다음 할 일/인계 메모)를 시간 역순으로 가져온다. "
            "'어디까지 했더라', '남은 작업 뭐였지'에 사용."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "project": {"type": "string", "description": "project_slug 부분 일치 필터(선택)"},
                "limit": {"type": "integer", "description": "최대 결과 수(기본 10)"},
            },
        },
    },
    {
        "name": "search_vault",
        "description": (
            "다른 머신에서 동기화된(pull된) vault(~/llm-wiki)의 결정·체크포인트·세션 요약을 "
            "전문 검색한다. **크로스머신** 회상 — 이 노트북이 아닌 다른 머신에서 내린 결정이나 "
            "작업 맥락을 찾을 때 사용. 로컬 전용인 recall_decisions/recent_checkpoints와 보완 관계. "
            "결과의 machine 라벨로 출처 머신을 확인."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "검색어(한글/영문, 모든 토큰 포함 AND)"},
                "machine": {"type": "string", "description": "머신 라벨 필터(선택, 정확 일치)"},
                "kind": {
                    "type": "string",
                    "description": "sessions|decisions|checkpoints 중 하나로 제한(선택)",
                },
                "limit": {"type": "integer", "description": "최대 결과 수(기본 20)"},
            },
            "required": ["query"],
        },
    },
]


def _tool_search_history(conn: sqlite3.Connection, args: dict) -> Any:
    return recall.search_history(
        conn,
        args["query"],
        project=args.get("project"),
        since=args.get("since"),
        limit=int(args.get("limit", 20)),
    )


def _tool_recall_decisions(conn: sqlite3.Connection, args: dict) -> Any:
    return recall.recall_decisions(
        conn,
        query=args.get("query"),
        project=args.get("project"),
        limit=int(args.get("limit", 20)),
    )


def _tool_recent_checkpoints(conn: sqlite3.Connection, args: dict) -> Any:
    return recall.recent_checkpoints(
        conn,
        project=args.get("project"),
        limit=int(args.get("limit", 10)),
    )


def _tool_search_vault(conn: sqlite3.Connection | None, args: dict) -> Any:
    # WHY: vault grep은 DB 불필요(파일 직독) — conn 무시.
    return recall.search_vault(
        args["query"],
        machine=args.get("machine"),
        kind=args.get("kind"),
        limit=int(args.get("limit", 20)),
    )


_DISPATCH: dict[str, Callable[[sqlite3.Connection, dict], Any]] = {
    "search_history": _tool_search_history,
    "recall_decisions": _tool_recall_decisions,
    "recent_checkpoints": _tool_recent_checkpoints,
    "search_vault": _tool_search_vault,
}

# DB 연결이 필요 없는 도구(vault 파일 직독) — conn 없이 호출.
_NO_DB_TOOLS = {"search_vault"}


def _ok(req_id: Any, result: Any) -> dict:
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def _err(req_id: Any, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}


def _text_result(payload: Any, is_error: bool = False) -> dict:
    """MCP tools/call 결과 — 텍스트(JSON 직렬화) 콘텐츠 1개."""
    text = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False, indent=2)
    return {"content": [{"type": "text", "text": text}], "isError": is_error}


def _call_tool(name: str, args: dict, conn_factory: Callable[[], sqlite3.Connection]) -> dict:
    fn = _DISPATCH.get(name)
    if fn is None:
        return _text_result(f"unknown tool: {name}", is_error=True)
    if name in _NO_DB_TOOLS:
        try:
            return _text_result(fn(None, args or {}))
        except (ValueError, OSError) as e:
            return _text_result(f"{type(e).__name__}: {e}", is_error=True)
    try:
        conn = conn_factory()
    except FileNotFoundError as e:
        return _text_result(
            f"세션 아카이브 DB가 아직 없습니다({e}). "
            "파이프라인을 1회 실행하세요: tools/session-archive/scripts/pipeline.sh",
            is_error=True,
        )
    try:
        result = fn(conn, args or {})
        return _text_result(result)
    except (ValueError, sqlite3.Error) as e:
        return _text_result(f"{type(e).__name__}: {e}", is_error=True)
    finally:
        conn.close()


def handle_message(msg: dict, conn_factory: Callable[[], sqlite3.Connection]) -> dict | None:
    """단일 JSON-RPC 메시지를 처리. 응답 dict 반환, 알림(notification)이면 None.

    conn_factory는 호출 시점에 읽기 전용 연결을 생성한다(주입 가능 → 테스트 용이).
    """
    if msg.get("jsonrpc") != "2.0":
        return _err(msg.get("id"), _INVALID_REQUEST, "jsonrpc must be '2.0'")

    method = msg.get("method")
    req_id = msg.get("id")
    is_notification = "id" not in msg

    if method == "initialize":
        client_proto = (msg.get("params") or {}).get("protocolVersion") or PROTOCOL_VERSION
        return _ok(req_id, {
            "protocolVersion": client_proto,
            "capabilities": {"tools": {}},
            "serverInfo": SERVER_INFO,
        })

    if method in ("notifications/initialized", "initialized"):
        return None  # 알림 — 응답 없음

    if method == "ping":
        return _ok(req_id, {})

    if method == "tools/list":
        return _ok(req_id, {"tools": TOOLS})

    if method == "tools/call":
        params = msg.get("params") or {}
        name = params.get("name")
        args = params.get("arguments") or {}
        return _ok(req_id, _call_tool(name, args, conn_factory))

    if is_notification:
        return None  # 모르는 알림은 조용히 무시
    return _err(req_id, _METHOD_NOT_FOUND, f"method not found: {method}")


def serve(
    stdin=None,
    stdout=None,
    conn_factory: Callable[[], sqlite3.Connection] | None = None,
) -> None:
    """stdio JSON-RPC 루프. 줄 단위로 메시지를 읽고 응답을 쓴다."""
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    conn_factory = conn_factory or recall.connect_ro

    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            _write(stdout, _err(None, _PARSE_ERROR, "parse error"))
            continue
        try:
            response = handle_message(msg, conn_factory)
        except Exception as e:  # noqa: BLE001 — 서버는 어떤 단일 메시지 오류로도 죽지 않는다
            response = _err(msg.get("id"), _INTERNAL_ERROR, f"{type(e).__name__}: {e}")
        if response is not None:
            _write(stdout, response)


def _write(stdout, obj: dict) -> None:
    stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    stdout.flush()


def main(argv: list[str] | None = None) -> int:
    serve()
    return 0


if __name__ == "__main__":
    sys.exit(main())
