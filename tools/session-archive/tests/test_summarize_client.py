"""summarize_client 단위 테스트 — 구조화 출력 강제 회귀 방지."""
import sqlite3

from session_archive.db import SCHEMA_SQL
from session_archive import summarize_client as sc


def _mk_db():
    conn = sqlite3.connect(":memory:", isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA_SQL.read_text(encoding="utf-8"))
    return conn


class _FakeBlock:
    type = "text"
    text = '{"intent":"i","outcome":"o","decisions":[],"tags":["t"],"files_touched":[],"quality_score":7}'


class _FakeUsage:
    input_tokens = 100
    output_tokens = 50


class _FakeResp:
    content = [_FakeBlock()]
    usage = _FakeUsage()
    stop_reason = "end_turn"


class _FakeMessages:
    def __init__(self):
        self.last_kwargs = None

    def create(self, **kwargs):
        self.last_kwargs = kwargs
        return _FakeResp()


class _FakeClient:
    def __init__(self):
        self.messages = _FakeMessages()


def test_call_model_passes_structured_output_schema():
    """call_model이 output_config(json_schema)를 전달해야 — json_parse_failed 회귀 방지."""
    conn = _mk_db()
    client = _FakeClient()

    result = sc.call_model(
        client, conn,
        model=sc.MODEL_DEFAULT, system="sys", user="usr", est_input_tokens=10,
    )

    kw = client.messages.last_kwargs
    assert kw is not None
    assert kw["output_config"] == {
        "format": {"type": "json_schema", "schema": sc.SUMMARY_SCHEMA}
    }
    # 유효 JSON이 파싱되어 error 없음
    assert result.error is None
    assert result.parsed["quality_score"] == 7


def test_summary_schema_is_well_formed():
    """구조화 출력 제약: 모든 object에 additionalProperties:false."""
    s = sc.SUMMARY_SCHEMA
    assert s["additionalProperties"] is False
    assert s["properties"]["decisions"]["items"]["additionalProperties"] is False
    assert set(s["required"]) == {
        "intent", "outcome", "decisions", "tags", "files_touched", "quality_score"
    }


def test_max_output_tokens_bumped():
    """산문 리드 후 JSON 잘림 방지 — 2048에서 상향."""
    assert sc.MAX_OUTPUT_TOKENS >= 4096
