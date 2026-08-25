"""Tests para LangfuseSource — sin red real (cliente HTTP fake)."""
import logging
import os
from types import SimpleNamespace
from unittest.mock import MagicMock

import httpx
import pytest

from obs_backend.sources.langfuse import LangfuseSource


# ---------------------------------------------------------------------------
# Helpers — fake httpx.Client
# ---------------------------------------------------------------------------

class _FakeResponse:
    def __init__(self, body: dict, raise_exc: Exception | None = None):
        self._body = body
        self._raise_exc = raise_exc

    def raise_for_status(self):
        if self._raise_exc is not None:
            raise self._raise_exc

    def json(self) -> dict:
        return self._body


class _FakeClient:
    """Imita httpx.Client con mínimo suficiente para LangfuseSource."""

    def __init__(self, response_body: dict):
        self._response = _FakeResponse(response_body)
        self.last_url: str | None = None
        self.last_params: dict | None = None
        self.init_base_url: str | None = None
        self.init_auth = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get(self, url: str, *, params: dict | None = None) -> _FakeResponse:
        self.last_url = url
        self.last_params = params
        return self._response


def _make_source(body: dict) -> tuple[LangfuseSource, _FakeClient]:
    fake = _FakeClient(body)

    def factory(base_url: str, auth):
        fake.init_base_url = base_url
        fake.init_auth = auth
        return fake

    return LangfuseSource(client_factory=factory), fake


# ---------------------------------------------------------------------------
# Fixture: trace completo con todos los campos
# ---------------------------------------------------------------------------

FULL_TRACE_ITEM = {
    "id": "trace-abc-123",
    "name": "chat.completion",
    "timestamp": "2026-06-19T10:00:00Z",
    "latency": 1.234,          # segundos → 1234 ms
    "totalTokens": 512,
    "userId": "user-007",
    "scores": [
        {"name": "faithfulness", "value": 0.87},
        {"name": "relevance", "value": 0.91},
    ],
}

SPARSE_TRACE_ITEM = {
    "id": "trace-sparse",
    # sin name, timestamp, latency, totalTokens, userId, scores
}


# ---------------------------------------------------------------------------
# Tests de parseo
# ---------------------------------------------------------------------------

def test_parseo_trace_completo():
    src, _ = _make_source({"data": [FULL_TRACE_ITEM]})
    result = src.recent_traces("qa", limit=10)

    assert len(result) == 1
    t = result[0]
    assert t.id == "trace-abc-123"
    assert t.name == "chat.completion"
    assert t.ts == "2026-06-19T10:00:00Z"
    assert t.latency_ms == pytest.approx(1234.0)   # latency seg → ms
    assert t.total_tokens == 512
    assert t.user_id == "user-007"
    assert t.faithfulness == pytest.approx(0.87)   # primer score con name=="faithfulness"


def test_parseo_trace_sin_campos_opcionales_no_rompe():
    src, _ = _make_source({"data": [SPARSE_TRACE_ITEM]})
    result = src.recent_traces("qa")

    assert len(result) == 1
    t = result[0]
    assert t.id == "trace-sparse"
    assert t.name == ""
    assert t.ts == ""
    assert t.latency_ms is None
    assert t.total_tokens is None
    assert t.user_id is None
    assert t.faithfulness is None


def test_faithfulness_ausente_cuando_no_hay_scores():
    item = {**FULL_TRACE_ITEM, "scores": []}
    src, _ = _make_source({"data": [item]})
    t = src.recent_traces("qa")[0]
    assert t.faithfulness is None


def test_faithfulness_ausente_cuando_no_hay_score_con_ese_nombre():
    item = {**FULL_TRACE_ITEM, "scores": [{"name": "relevance", "value": 0.9}]}
    src, _ = _make_source({"data": [item]})
    t = src.recent_traces("qa")[0]
    assert t.faithfulness is None


def test_faithfulness_primer_match_cuando_hay_varios():
    item = {
        **FULL_TRACE_ITEM,
        "scores": [
            {"name": "faithfulness", "value": 0.50},
            {"name": "faithfulness", "value": 0.99},
        ],
    }
    src, _ = _make_source({"data": [item]})
    t = src.recent_traces("qa")[0]
    assert t.faithfulness == pytest.approx(0.50)


# ---------------------------------------------------------------------------
# Tests de autenticación
# ---------------------------------------------------------------------------

def test_basic_auth_cuando_LANGFUSE_OBS_KEY_esta_seteado(monkeypatch):
    monkeypatch.setenv("LANGFUSE_OBS_KEY", "pk-pub-key:sk-secret-key")
    src, fake = _make_source({"data": []})
    src.recent_traces("qa")
    assert fake.init_auth == ("pk-pub-key", "sk-secret-key")


def test_sin_auth_cuando_LANGFUSE_OBS_KEY_vacio(monkeypatch):
    monkeypatch.delenv("LANGFUSE_OBS_KEY", raising=False)
    src, fake = _make_source({"data": []})
    src.recent_traces("qa")
    assert fake.init_auth is None


# ---------------------------------------------------------------------------
# Tests de endpoint y params correctos
# ---------------------------------------------------------------------------

def test_pega_al_endpoint_correcto():
    src, fake = _make_source({"data": []})
    src.recent_traces("qa", limit=15)

    assert fake.last_url == "/api/public/traces"
    assert fake.last_params == {"limit": 15, "orderBy": "timestamp.desc"}


def test_base_url_es_la_de_qa():
    src, fake = _make_source({"data": []})
    src.recent_traces("qa")
    assert fake.init_base_url == "http://langfuse-qa.macro.com.ar"


def test_base_url_es_la_de_prod():
    src, fake = _make_source({"data": []})
    src.recent_traces("prod")
    assert fake.init_base_url == "http://langfuse-prod.macro.com.ar"


def test_multiples_traces_devueltos():
    body = {"data": [FULL_TRACE_ITEM, SPARSE_TRACE_ITEM]}
    src, _ = _make_source(body)
    result = src.recent_traces("qa", limit=20)
    assert len(result) == 2
    assert result[0].id == "trace-abc-123"
    assert result[1].id == "trace-sparse"


# ---------------------------------------------------------------------------
# Auth robusto
# ---------------------------------------------------------------------------

def test_key_con_espacios_y_newline_se_recorta(monkeypatch):
    monkeypatch.setenv("LANGFUSE_OBS_KEY", "  pk-pub:sk-sec\n")
    src, fake = _make_source({"data": []})
    src.recent_traces("qa")
    assert fake.init_auth == ("pk-pub", "sk-sec")


def test_secret_con_dos_puntos_sobrevive(monkeypatch):
    monkeypatch.setenv("LANGFUSE_OBS_KEY", "pk-pub:sk:con:colons")
    src, fake = _make_source({"data": []})
    src.recent_traces("qa")
    assert fake.init_auth == ("pk-pub", "sk:con:colons")


def test_key_sin_dos_puntos_lanza_error(monkeypatch):
    monkeypatch.setenv("LANGFUSE_OBS_KEY", "solo-publica-sin-secreto")
    src, _ = _make_source({"data": []})
    with pytest.raises(ValueError):
        src.recent_traces("qa")


# ---------------------------------------------------------------------------
# Valores falsy-pero-válidos: los ceros legítimos no se descartan
# ---------------------------------------------------------------------------

def test_latency_cero_se_preserva():
    item = {**FULL_TRACE_ITEM, "latency": 0}
    src, _ = _make_source({"data": [item]})
    assert src.recent_traces("qa")[0].latency_ms == 0.0


def test_faithfulness_cero_se_preserva():
    item = {**FULL_TRACE_ITEM, "scores": [{"name": "faithfulness", "value": 0.0}]}
    src, _ = _make_source({"data": [item]})
    assert src.recent_traces("qa")[0].faithfulness == 0.0


def test_total_tokens_cero_se_preserva():
    item = {**FULL_TRACE_ITEM, "totalTokens": 0}
    src, _ = _make_source({"data": [item]})
    assert src.recent_traces("qa")[0].total_tokens == 0


# ---------------------------------------------------------------------------
# Resiliencia: shapes malformados del sistema observado no rompen
# ---------------------------------------------------------------------------

def test_payload_no_dict_devuelve_vacio():
    src, _ = _make_source(["no", "soy", "un", "dict"])  # type: ignore[arg-type]
    assert src.recent_traces("qa") == []


def test_payload_sin_data_devuelve_vacio():
    src, _ = _make_source({})
    assert src.recent_traces("qa") == []


def test_data_no_es_lista_devuelve_vacio():
    src, _ = _make_source({"data": {"oops": "dict"}})
    assert src.recent_traces("qa") == []


def test_item_no_dict_se_saltea():
    src, _ = _make_source({"data": ["no-soy-un-dict", FULL_TRACE_ITEM]})
    result = src.recent_traces("qa")
    assert len(result) == 1
    assert result[0].id == "trace-abc-123"


def test_scores_no_es_lista_no_rompe():
    item = {**FULL_TRACE_ITEM, "scores": {"name": "faithfulness", "value": 0.9}}
    src, _ = _make_source({"data": [item]})
    assert src.recent_traces("qa")[0].faithfulness is None


def test_latency_string_no_se_multiplica():
    item = {**FULL_TRACE_ITEM, "latency": "1.2"}
    src, _ = _make_source({"data": [item]})
    assert src.recent_traces("qa")[0].latency_ms is None


def test_total_tokens_float_se_coerciona_a_int():
    item = {**FULL_TRACE_ITEM, "totalTokens": 511.9}
    src, _ = _make_source({"data": [item]})
    assert src.recent_traces("qa")[0].total_tokens == 511


# ---------------------------------------------------------------------------
# Errores de transporte: degradan a vacío + WARNING conciso (sin traceback)
# ---------------------------------------------------------------------------

def test_recent_traces_degrada_a_vacio_en_transport_error(caplog):
    class _RaisingClient:
        def __enter__(self): return self
        def __exit__(self, *exc): return False
        def get(self, *a, **k):
            raise httpx.ConnectError("Name or service not known")

    src = LangfuseSource(client_factory=lambda base_url, auth: _RaisingClient())
    with caplog.at_level(logging.WARNING):
        result = src.recent_traces("dev")

    assert result == []
    assert any(
        r.levelno == logging.WARNING and "inalcanzable" in r.getMessage().lower()
        for r in caplog.records
    ), "debe logear un WARNING conciso"
    assert not any(r.levelno >= logging.ERROR for r in caplog.records), "nada en ERROR"
    assert not any(r.exc_info for r in caplog.records), "sin traceback"


def test_recent_traces_5xx_propaga():
    """Un HTTPStatusError real (p.ej. 500) NO es un TransportError: debe propagar,
    no degradar a vacío. warn_unreachable() sólo atrapa fallas de transporte."""
    req = httpx.Request("GET", "http://x/api/public/traces")
    err = httpx.HTTPStatusError("500", request=req, response=httpx.Response(500, request=req))

    class _RaisingStatusClient:
        def __enter__(self): return self
        def __exit__(self, *exc): return False
        def get(self, *a, **k):
            return _FakeResponse({}, raise_exc=err)

    src = LangfuseSource(client_factory=lambda base_url, auth: _RaisingStatusClient())
    with pytest.raises(httpx.HTTPStatusError):
        src.recent_traces("dev")
