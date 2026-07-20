import httpx
import pytest

from obs_backend.config import ENVIRONMENTS
from obs_backend.sources import _rag


def test_environments_have_rag_base_url():
    for env in ("dev", "qa", "prod"):
        assert ENVIRONMENTS[env]["rag_base_url"].startswith("http")


def test_rag_token_for_reads_env_var(monkeypatch):
    monkeypatch.setenv("RAG_OBS_TOKEN_DEV", "  jwt-abc\n")
    assert _rag.rag_token_for("dev") == "jwt-abc"


def test_rag_token_for_missing_returns_none(monkeypatch):
    monkeypatch.delenv("RAG_OBS_TOKEN_QA", raising=False)
    assert _rag.rag_token_for("qa") is None


def test_client_factory_sets_cookie_when_token_present():
    client = _rag.default_rag_client_factory("http://rag.example", "jwt-abc")
    try:
        assert client.headers["cookie"] == "access_token=jwt-abc"
    finally:
        client.close()


def test_client_factory_no_cookie_when_token_absent():
    client = _rag.default_rag_client_factory("http://rag.example", None)
    try:
        assert "cookie" not in {k.lower() for k in client.headers}
    finally:
        client.close()
