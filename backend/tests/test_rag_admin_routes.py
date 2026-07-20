import pytest
from fastapi.testclient import TestClient

from obs_backend.api import create_app


class FakeAdmin:
    def __init__(self):
        self.calls = []

    def get(self, env, path, params=None):
        self.calls.append((env, path, params))
        return {"items": [{"document_name": "003 Personas – Beneficios.pdf",
                           "area": "GCIA PERSONAS", "version": "v2",
                           "usage_count": 249, "unique_users": 10,
                           "positive_count": 0, "negative_count": 0}],
                "total": 1, "page": 1, "page_size": 20}


class NoneAdmin:
    """Simulates an upstream RAG failure (HTTP error/timeout/bad shape)."""

    def get(self, env, path, params=None):
        return None


class EmptyAdmin:
    """Simulates a genuinely empty (but successful) upstream response."""

    def get(self, env, path, params=None):
        return {"items": [], "total": 0}


ADMIN_PATHS = [
    "/v1/{env}/admin/supervision/documents",
    "/v1/{env}/admin/supervision/documents/8801/chunks",
    "/v1/{env}/admin/reporteria",
    "/v1/{env}/admin/modelos",
    "/v1/{env}/admin/prompts",
    "/v1/{env}/admin/identidad",
]


def _client(fake):
    # Provide explicit stubs for every source so no real GCP/Langfuse import fires.
    app = create_app(
        log_source=object(), trace_source=object(), metrics_source=object(),
        workload_source=object(), events_source=object(), rag_source=object(),
        rag_admin_source=fake,
    )
    return TestClient(app)


def test_supervision_documents_ok():
    fake = FakeAdmin()
    r = _client(fake).get("/v1/dev/admin/supervision/documents", params={"page": 1})
    assert r.status_code == 200
    assert r.json()["items"][0]["usage_count"] == 249
    assert fake.calls[0][1] == "/api/v1/admin/governance/documents"


def test_supervision_prod_rejected():
    r = _client(FakeAdmin()).get("/v1/prod/admin/supervision/documents")
    assert r.status_code == 404


def test_supervision_unknown_env_rejected():
    r = _client(FakeAdmin()).get("/v1/staging/admin/supervision/documents")
    assert r.status_code == 404


def test_supervision_chunks_forwards_id():
    fake = FakeAdmin()
    _client(fake).get("/v1/qa/admin/supervision/documents/8801/chunks")
    assert fake.calls[0][1] == "/api/v1/admin/governance/documents/8801/chunks"


def test_reporteria_forwards_dates():
    fake = FakeAdmin()
    _client(fake).get("/v1/dev/admin/reporteria",
                      params={"date_from": "2026-07-01", "date_to": "2026-07-20"})
    assert fake.calls[0][1] == "/api/v1/analytics/dashboard/executive"
    assert fake.calls[0][2]["date_from"] == "2026-07-01"


def test_modelos_route():
    fake = FakeAdmin()
    _client(fake).get("/v1/qa/admin/modelos")
    assert fake.calls[0][1] == "/api/v1/admin/model-routing"


def test_prompts_route():
    fake = FakeAdmin()
    _client(fake).get("/v1/dev/admin/prompts")
    assert fake.calls[0][1] == "/api/v1/admin/prompts"


def test_identidad_route():
    fake = FakeAdmin()
    _client(fake).get("/v1/qa/admin/identidad")
    assert fake.calls[0][1] == "/api/v1/admin/ad-group-mappings/"


@pytest.mark.parametrize("path_template", ADMIN_PATHS)
def test_admin_get_upstream_error_502(path_template):
    r = _client(NoneAdmin()).get(path_template.format(env="dev"),
                                 params={"date_from": "2026-07-01", "date_to": "2026-07-20"})
    assert r.status_code == 502


@pytest.mark.parametrize("path_template", ADMIN_PATHS)
def test_admin_get_empty_list_is_200(path_template):
    r = _client(EmptyAdmin()).get(path_template.format(env="dev"),
                                  params={"date_from": "2026-07-01", "date_to": "2026-07-20"})
    assert r.status_code == 200
    assert r.json() == {"items": [], "total": 0}


@pytest.mark.parametrize("env", ["prod", "staging"])
@pytest.mark.parametrize("path_template", ADMIN_PATHS)
def test_admin_env_guard_rejects_prod_and_unknown(path_template, env):
    r = _client(FakeAdmin()).get(path_template.format(env=env),
                                 params={"date_from": "2026-07-01", "date_to": "2026-07-20"})
    assert r.status_code == 404


def test_admin_response_has_no_token():
    fake = FakeAdmin()
    r = _client(fake).get("/v1/dev/admin/supervision/documents")
    assert r.status_code == 200
    body_text = r.text
    assert "access_token" not in body_text
    for k, v in r.headers.items():
        assert "access_token" not in k.lower()
        assert "access_token" not in str(v)
    # the fake never receives a token itself, but guard against it leaking
    # from headers/body if a real source were swapped in.
    assert "jwt" not in body_text.lower()
