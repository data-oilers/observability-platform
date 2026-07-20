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
