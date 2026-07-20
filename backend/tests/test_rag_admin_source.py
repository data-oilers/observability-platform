import httpx
import pytest

from obs_backend.sources.rag_admin import RagAdminSource


def _factory(handler):
    def make(base_url, token):
        transport = httpx.MockTransport(handler)
        return httpx.Client(base_url=base_url, transport=transport)
    return make


def test_get_unwraps_envelope():
    def handler(request):
        assert request.url.path == "/api/v1/admin/governance/documents"
        return httpx.Response(200, json={"data": {"items": [{"document_name": "x"}]}, "error": None, "meta": {}})

    src = RagAdminSource(client_factory=_factory(handler), token_for=lambda env: "jwt")
    out = src.get("dev", "/api/v1/admin/governance/documents")
    assert out == {"items": [{"document_name": "x"}]}


def test_get_http_error_returns_none():
    def handler(request):
        return httpx.Response(403, json={"data": None, "error": "forbidden", "meta": {}})

    src = RagAdminSource(client_factory=_factory(handler), token_for=lambda env: "jwt")
    assert src.get("dev", "/api/v1/admin/governance/documents") is None


def test_get_bad_shape_returns_none():
    def handler(request):
        return httpx.Response(200, text="not-json")

    src = RagAdminSource(client_factory=_factory(handler), token_for=lambda env: "jwt")
    assert src.get("dev", "/whatever") is None


def test_get_passes_params():
    seen = {}

    def handler(request):
        seen["q"] = dict(request.url.params)
        return httpx.Response(200, json={"data": [], "error": None, "meta": {}})

    src = RagAdminSource(client_factory=_factory(handler), token_for=lambda env: "jwt")
    src.get("dev", "/x", params={"page": 2, "page_size": 20})
    assert seen["q"] == {"page": "2", "page_size": "20"}
