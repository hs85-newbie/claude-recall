"""mcp_server.py JSON-RPC 처리 통합 테스트 — stdio 루프 + 메시지 핸들러."""
import io
import json
import sqlite3

from session_archive import mcp_server
from session_archive.db import SCHEMA_SQL


def _mk_db():
    conn = sqlite3.connect(":memory:", isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA_SQL.read_text(encoding="utf-8"))
    return conn


def _seed(conn):
    conn.execute(
        "INSERT INTO sessions (session_id, project_dir, project_slug, started_at, source_file, source_mtime) "
        "VALUES ('s1', '/x/proj-a', 'proj-a', '2026-06-10T00:00:00Z', 'f.jsonl', 0)",
    )
    conn.execute(
        "INSERT INTO events (session_id, uuid, type, timestamp, role, content) "
        "VALUES ('s1', 'u1', 'message', '2026-06-10T00:00:00Z', 'user', '토큰 대시보드')",
    )
    conn.execute("INSERT INTO events_fts (uuid, session_id, content) VALUES ('u1', 's1', '토큰 대시보드')")
    conn.execute(
        "INSERT INTO checkpoints (checkpoint_id, machine, project_slug, title, content, created_at, source_file, source_mtime) "
        "VALUES ('m::proj-a::cp.md', 'm', 'proj-a', 'handoff', '다음 작업 토큰', '2026-06-11T00:00:00Z', 'f.md', 0)",
    )
    conn.execute("INSERT INTO checkpoints_fts (checkpoint_id, content) VALUES ('m::proj-a::cp.md', '다음 작업 토큰')")
    conn.execute(
        "INSERT INTO session_summaries (session_id, intent, decisions_json, model, summarized_at, quality_score) "
        "VALUES ('s1', 'x', '[{\"decision\": \"STT Clova\", \"rationale\": \"정확도\"}]', 'haiku', '2026-06-10T01:00:00Z', 8)",
    )
    return conn


def _factory():
    return _seed(_mk_db())


def _handle(method, params=None, req_id=1, factory=_factory):
    msg = {"jsonrpc": "2.0", "id": req_id, "method": method}
    if params is not None:
        msg["params"] = params
    return mcp_server.handle_message(msg, factory)


# ── protocol handshake ──

def test_initialize_echoes_protocol_and_advertises_tools():
    resp = _handle("initialize", {"protocolVersion": "2025-06-18"})
    assert resp["result"]["protocolVersion"] == "2025-06-18"
    assert "tools" in resp["result"]["capabilities"]
    assert resp["result"]["serverInfo"]["name"] == "session-archive-recall"


def test_initialized_notification_returns_none():
    msg = {"jsonrpc": "2.0", "method": "notifications/initialized"}
    assert mcp_server.handle_message(msg, _factory) is None


def test_ping():
    assert _handle("ping")["result"] == {}


def test_tools_list_has_four_tools():
    resp = _handle("tools/list")
    names = {t["name"] for t in resp["result"]["tools"]}
    assert names == {"search_history", "recall_decisions", "recent_checkpoints", "search_vault"}


def test_unknown_method_returns_error():
    resp = _handle("does/not/exist")
    assert resp["error"]["code"] == -32601


def test_bad_jsonrpc_version_rejected():
    resp = mcp_server.handle_message({"jsonrpc": "1.0", "id": 1, "method": "ping"}, _factory)
    assert resp["error"]["code"] == -32600


# ── tools/call ──

def _call(name, arguments=None):
    resp = _handle("tools/call", {"name": name, "arguments": arguments or {}})
    content = resp["result"]["content"][0]["text"]
    return resp["result"], json.loads(content) if not resp["result"]["isError"] else content


def test_call_search_history():
    result, payload = _call("search_history", {"query": "토큰"})
    assert result["isError"] is False
    assert len(payload["events"]) == 1
    assert len(payload["checkpoints"]) == 1


def test_call_recall_decisions():
    result, payload = _call("recall_decisions", {})
    assert result["isError"] is False
    assert payload[0]["decision"] == "STT Clova"


def test_call_recent_checkpoints():
    result, payload = _call("recent_checkpoints", {})
    assert result["isError"] is False
    assert payload[0]["title"] == "handoff"


def test_call_search_vault(tmp_path, monkeypatch):
    # vault grep은 DB 불필요 — DEFAULT_VAULT_ROOT를 tmp로 가리킴
    (tmp_path / "decisions").mkdir(parents=True)
    (tmp_path / "decisions" / "d1.md").write_text(
        "---\nmachine: machineB\n---\n# 결정\nSTT는 Clova\n", encoding="utf-8",
    )
    monkeypatch.setattr(mcp_server.recall, "DEFAULT_VAULT_ROOT", tmp_path)
    result, payload = _call("search_vault", {"query": "Clova"})
    assert result["isError"] is False
    assert len(payload) == 1
    assert payload[0]["machine"] == "machineB"


def test_call_search_vault_works_without_db(tmp_path, monkeypatch):
    # DB가 없어도(연결 팩토리가 던져도) search_vault는 동작해야 한다
    (tmp_path / "checkpoints" / "machineA").mkdir(parents=True)
    (tmp_path / "checkpoints" / "machineA" / "cp.md").write_text("토큰 인덱스\n", encoding="utf-8")
    monkeypatch.setattr(mcp_server.recall, "DEFAULT_VAULT_ROOT", tmp_path)

    def dead_factory():
        raise FileNotFoundError("/no/db")

    resp = mcp_server.handle_message(
        {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
         "params": {"name": "search_vault", "arguments": {"query": "토큰"}}},
        dead_factory,
    )
    assert resp["result"]["isError"] is False
    payload = json.loads(resp["result"]["content"][0]["text"])
    assert payload[0]["machine"] == "machineA"


def test_call_unknown_tool_is_error():
    result, _ = _call("nope")
    assert result["isError"] is True


def test_call_missing_db_is_friendly_error():
    def bad_factory():
        raise FileNotFoundError("/x/sessions.db")

    resp = mcp_server.handle_message(
        {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
         "params": {"name": "search_history", "arguments": {"query": "x"}}},
        bad_factory,
    )
    assert resp["result"]["isError"] is True
    assert "pipeline.sh" in resp["result"]["content"][0]["text"]


# ── stdio loop ──

def test_serve_loop_processes_lines():
    stdin = io.StringIO(
        json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}) + "\n"
        + json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n"
        + json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}) + "\n"
    )
    stdout = io.StringIO()
    mcp_server.serve(stdin=stdin, stdout=stdout, conn_factory=_factory)
    lines = [json.loads(x) for x in stdout.getvalue().splitlines() if x.strip()]
    # initialize(id=1) + tools/list(id=2) 응답 2개. notification은 응답 없음.
    assert [l["id"] for l in lines] == [1, 2]


def test_serve_loop_parse_error_does_not_crash():
    stdin = io.StringIO("{bad json\n" + json.dumps({"jsonrpc": "2.0", "id": 5, "method": "ping"}) + "\n")
    stdout = io.StringIO()
    mcp_server.serve(stdin=stdin, stdout=stdout, conn_factory=_factory)
    lines = [json.loads(x) for x in stdout.getvalue().splitlines() if x.strip()]
    assert lines[0]["error"]["code"] == -32700
    assert lines[1]["id"] == 5
