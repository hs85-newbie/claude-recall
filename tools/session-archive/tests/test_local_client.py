"""local_client 단위 테스트 — urlopen mock으로 HTTP 경로 차단."""
import io
import json
import urllib.error

import pytest

from session_archive import local_client


class _FakeResponse:
    def __init__(self, body: bytes, status: int = 200):
        self._body = body
        self.status = status

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _mk_success(text="hello", in_tok=100, out_tok=50):
    return json.dumps({
        "choices": [{"message": {"content": text}}],
        "usage": {"prompt_tokens": in_tok, "completion_tokens": out_tok},
    }).encode("utf-8")


def test_ping_true(monkeypatch):
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda url, timeout=None: _FakeResponse(b"{}", status=200),
    )
    assert local_client.ping() is True


def test_ping_false_on_connection_error(monkeypatch):
    def boom(*a, **kw):
        raise ConnectionError("refused")

    monkeypatch.setattr("urllib.request.urlopen", boom)
    assert local_client.ping() is False


def test_call_local_success(monkeypatch):
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda req, timeout=None: _FakeResponse(_mk_success("{\"intent\":\"x\"}")),
    )
    r = local_client.call_local("sys", "usr")
    assert r.error is None
    assert r.text == '{"intent":"x"}'
    assert r.input_tokens == 100
    assert r.output_tokens == 50


def test_call_local_http_error(monkeypatch):
    def boom(req, timeout=None):
        raise urllib.error.HTTPError(
            url="http://x", code=503, msg="down", hdrs=None, fp=io.BytesIO(b"")
        )

    monkeypatch.setattr("urllib.request.urlopen", boom)
    r = local_client.call_local("s", "u")
    assert r.error and "HTTPError 503" in r.error
    assert r.text == ""


def test_call_local_connection_error(monkeypatch):
    def boom(req, timeout=None):
        raise ConnectionError("refused")

    monkeypatch.setattr("urllib.request.urlopen", boom)
    r = local_client.call_local("s", "u")
    assert r.error and "ConnectionError" in r.error


def test_call_local_invalid_json(monkeypatch):
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda req, timeout=None: _FakeResponse(b"not json"),
    )
    r = local_client.call_local("s", "u")
    assert r.error and "invalid JSON" in r.error


def test_call_local_empty_choices(monkeypatch):
    body = json.dumps({"choices": [], "usage": {}}).encode("utf-8")
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda req, timeout=None: _FakeResponse(body),
    )
    r = local_client.call_local("s", "u")
    assert r.error == "empty_response"
