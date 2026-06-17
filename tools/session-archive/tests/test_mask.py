"""마스킹 규칙 테스트 (ADR-001 §2.7).

fixture 20개 이상 — 각 규칙의 positive/negative 샘플 포함.
"""
from __future__ import annotations

import pytest

from session_archive.mask import mask_text


# ---------------- positive cases ----------------

def test_openai_key_일반형식_마스킹되어야_한다():
    r = mask_text("key=sk-proj-AbCdEfGhIjKlMnOpQrStUv next")
    assert "sk-proj" not in r.text
    assert "[REDACTED" in r.text
    assert r.masked


def test_anthropic_key_마스킹되어야_한다():
    r = mask_text("ANTHROPIC=sk-ant-api03-abcdefghijklmnopqrstuvwxyz1234567890_-AB")
    assert "sk-ant-" not in r.text
    # env_var 규칙이 먼저 잡아서 ENV로 치환되거나, 이후 API_KEY로 치환됨 — 둘 다 OK
    assert "[REDACTED" in r.text


def test_github_classic_pat_마스킹되어야_한다():
    token = "ghp_" + "a" * 36
    r = mask_text(f"token {token} end")
    assert "ghp_aaaa" not in r.text
    assert "[REDACTED:GH_TOKEN]" in r.text


def test_github_fine_grained_pat_마스킹되어야_한다():
    token = "github_pat_" + "a" * 82
    r = mask_text(f"use {token}")
    assert token not in r.text
    assert "[REDACTED:GH_TOKEN]" in r.text


def test_aws_access_key_마스킹되어야_한다():
    r = mask_text("AWS_KEY AKIAIOSFODNN7EXAMPLE rest")
    assert "AKIAIOSFODNN7EXAMPLE" not in r.text
    # env_var로 먼저 잡힐 수도 있음
    assert r.masked


def test_jwt_마스킹되어야_한다():
    jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NSJ9.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
    r = mask_text(f"bearer {jwt}")
    assert jwt not in r.text
    assert "[REDACTED:JWT]" in r.text


def test_env_var_PASSWORD_값만_치환되어야_한다():
    r = mask_text("DB_PASSWORD=sup3rSecret!")
    assert "sup3rSecret" not in r.text
    assert "DB_PASSWORD" in r.text  # 키는 유지
    assert "[REDACTED:ENV]" in r.text


def test_env_var_SECRET_대소문자_무관():
    r = mask_text("my_secret: plaintext_value")
    assert "plaintext_value" not in r.text
    assert "my_secret" in r.text


def test_env_var_API_KEY_하이픈_언더스코어_둘다():
    r1 = mask_text("API_KEY=abc123XYZ__")
    r2 = mask_text("api-key: abc123XYZ__")
    assert r1.masked and r2.masked
    assert "abc123XYZ" not in r1.text
    assert "abc123XYZ" not in r2.text


def test_여러_시크릿_한_텍스트에_혼재():
    text = (
        "AWS=AKIAIOSFODNN7EXAMPLE "
        "GH=ghp_" + "x" * 36 + " "
        "ANTH=sk-ant-abcdefghijklmnopqrstuv"
    )
    r = mask_text(text)
    assert "AKIA" not in r.text
    assert "ghp_xxxx" not in r.text
    assert "sk-ant-" not in r.text
    assert sum(r.hits.values()) >= 3


def test_email_기본은_OFF():
    r = mask_text("contact me at user@example.com please")
    assert "user@example.com" in r.text
    assert r.hits.get("email", 0) == 0


def test_email_옵트인_시_마스킹():
    r = mask_text("contact user@example.com", mask_email=True)
    assert "user@example.com" not in r.text
    assert "[REDACTED:EMAIL]" in r.text


# ---------------- negative / false-positive 방지 ----------------

def test_일반_코드는_건드리지_않는다():
    text = "const result = await fetch('/api/v1/users')"
    r = mask_text(text)
    assert r.text == text
    assert not r.masked


def test_sk_로_시작해도_짧으면_무시():
    # sk-short는 API 키 아님
    r = mask_text("sk-short")
    assert r.text == "sk-short"
    assert not r.masked


def test_파일경로는_email_off에서_영향없음():
    text = "see /Users/you/.claude/projects/foo.jsonl"
    r = mask_text(text)
    assert r.text == text


def test_AKIA_접두_일반단어는_무시():
    # AKIA 다음 16자 정확히 대문자/숫자가 아니면 매치 안 됨
    r = mask_text("AKIAtest this is not a key")
    assert not r.masked


def test_empty_string():
    r = mask_text("")
    assert r.text == ""
    assert not r.masked


def test_None_처리_방어():
    r = mask_text(None)  # type: ignore[arg-type]
    assert not r.masked


def test_env_var_주석형태_토큰_다음은_중단():
    r = mask_text("API_KEY=abc123XYZ__ # comment")
    assert "abc123XYZ" not in r.text
    assert "# comment" in r.text  # 주석은 유지


def test_env_var_쉘_export_스타일():
    r = mask_text("export DATABASE_PASSWORD=hunter2")
    assert "hunter2" not in r.text
    assert "DATABASE_PASSWORD" in r.text


def test_MaskResult_hits_카운트_정확():
    token = "ghp_" + "a" * 36
    r = mask_text(f"{token} and {token}")
    assert r.hits["github_token"] == 2


def test_여러줄_env_var_각각_잡힌다():
    text = "PASSWORD=aaa\nTOKEN=bbb\nnormal_line"
    r = mask_text(text)
    assert "aaa" not in r.text
    assert "bbb" not in r.text
    assert "normal_line" in r.text
    assert r.hits["env_var"] >= 2


def test_JSON_스타일_키_값():
    r = mask_text('{"api_key": "abc123XYZ__"}')
    # JSON의 콜론+공백+값도 env_var 패턴으로 잡힘
    assert "abc123XYZ" not in r.text
