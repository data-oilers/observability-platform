"""Tests para RagPipelineSource — sin red real (cliente HTTP fake)."""
import logging

import httpx
import pytest

from obs_backend.sources.rag_pipeline import RagPipelineSource


# ---------------------------------------------------------------------------
# Helpers — fake httpx.Client con soporte paginado
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


class _FakeObsClient:
    """Supports paginated responses: list of response bodies per page.

    An entry may also be an Exception instance, in which case get() raises it
    for that page instead of returning a response (used to simulate a
    transport error mid-pagination).
    """

    def __init__(self, pages: list[dict | Exception]):
        self._pages = pages  # index 0 = page 1, index 1 = page 2, etc.
        self.requests: list[dict] = []  # captures each (url, params) call
        self.init_base_url: str | None = None
        self.init_auth = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get(self, url: str, *, params: dict | None = None):
        self.requests.append({"url": url, "params": params or {}})
        page_num = params.get("page", 1) if params else 1
        idx = page_num - 1
        entry = self._pages[idx] if idx < len(self._pages) else {"data": []}
        if isinstance(entry, Exception):
            raise entry
        return _FakeResponse(entry)


def _make_source(pages: list[dict | Exception]) -> tuple[RagPipelineSource, _FakeObsClient]:
    fake = _FakeObsClient(pages)

    def factory(base_url: str, auth):
        fake.init_base_url = base_url
        fake.init_auth = auth
        return fake

    return RagPipelineSource(client_factory=factory), fake


def _obs(name="retriever", latency=100.0, level="DEFAULT", usage=None, start=None, end=None):
    """Helper para construir un observation dict."""
    item: dict = {"name": name, "level": level}
    if latency is not None:
        item["latency"] = latency
    if usage is not None:
        item["usage"] = usage
    if start is not None:
        item["startTime"] = start
    if end is not None:
        item["endTime"] = end
    return item


# ---------------------------------------------------------------------------
# Agregación básica
# ---------------------------------------------------------------------------

def test_aggregation_by_name_counts_calls():
    """3 observations con el mismo nombre → calls=3."""
    obs = [_obs("retriever"), _obs("retriever"), _obs("retriever")]
    src, _ = _make_source([{"data": obs}])
    result = src.rag_node_stats("qa")
    assert len(result) == 1
    assert result[0].node == "retriever"
    assert result[0].calls == 3


def test_aggregation_by_name_p50_p95():
    """Latencias conocidas → p50 y p95 correctos.

    El campo latency es en SEGUNDOS; el código lo convierte a ms (*1000).
    10 obs con latency 0.01..0.10 s → 10..100 ms → p50=50 ms, p95=100 ms.
    """
    # latency en segundos: 0.01, 0.02, ..., 0.10 → 10, 20, ..., 100 ms
    obs = [_obs("retriever", latency=float(i) / 100) for i in range(1, 11)]
    src, _ = _make_source([{"data": obs}])
    result = src.rag_node_stats("qa")
    assert len(result) == 1
    stat = result[0]
    # nearest-rank: p50 de 10 valores = índice ceil(0.5*10)=5 → valor en pos 5 = 50.0
    assert stat.p50_ms == pytest.approx(50.0)
    # p95 de 10 valores = índice ceil(0.95*10)=10 → valor en pos 10 = 100.0
    assert stat.p95_ms == pytest.approx(100.0)


# ---------------------------------------------------------------------------
# Latencia desde distintos campos
# ---------------------------------------------------------------------------

def test_latency_from_start_end_time():
    """obs sin campo latency pero con startTime/endTime → calcula latencia."""
    obs = [_obs("node", latency=None,
                start="2026-06-20T10:00:00Z",
                end="2026-06-20T10:00:00.200Z")]
    src, _ = _make_source([{"data": obs}])
    result = src.rag_node_stats("qa")
    assert len(result) == 1
    # 200 ms de diferencia
    assert result[0].p50_ms == pytest.approx(200.0, abs=1.0)


def test_latency_from_numeric_latency_field():
    """obs con campo latency numérico sin timestamps → se interpreta como SEGUNDOS (*1000 → ms)."""
    obs = [_obs("node", latency=1.2)]
    src, _ = _make_source([{"data": obs}])
    result = src.rag_node_stats("qa")
    assert result[0].p50_ms == pytest.approx(1200.0)


def test_latency_field_is_seconds():
    """latency=0.35 sin timestamps → 350.0 ms (no 0.35)."""
    obs = [_obs("node", latency=0.35)]
    src, _ = _make_source([{"data": obs}])
    result = src.rag_node_stats("qa")
    assert result[0].p50_ms == pytest.approx(350.0)


def test_latency_prefers_start_end_over_latency_field():
    """Con AMBOS latency y startTime/endTime → se usa el delta start/end (1.2 s = 1200 ms)."""
    obs = [_obs(
        "node",
        latency=1.2,
        start="2026-06-20T10:00:00.000Z",
        end="2026-06-20T10:00:01.200Z",
    )]
    src, _ = _make_source([{"data": obs}])
    result = src.rag_node_stats("qa")
    # start/end delta = 1200 ms; latency field (también 1.2 s = 1200 ms) → coinciden
    assert result[0].p50_ms == pytest.approx(1200.0)


def test_latency_zero_preserved():
    """latency=0.0 → p50=0.0 (no None)."""
    obs = [_obs("node", latency=0.0)]
    src, _ = _make_source([{"data": obs}])
    result = src.rag_node_stats("qa")
    assert result[0].p50_ms == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# Observaciones sin nombre → descartadas
# ---------------------------------------------------------------------------

def test_nameless_obs_skipped():
    """obs con name='' o name=None → no se cuentan."""
    obs = [
        {"name": "", "latency": 100.0, "level": "DEFAULT"},
        {"name": None, "latency": 100.0, "level": "DEFAULT"},
        _obs("valid"),
    ]
    src, _ = _make_source([{"data": obs}])
    result = src.rag_node_stats("qa")
    assert len(result) == 1
    assert result[0].node == "valid"
    assert result[0].calls == 1


# ---------------------------------------------------------------------------
# Errores
# ---------------------------------------------------------------------------

def test_error_counted_on_level_error():
    """obs con level='ERROR' → errors=1."""
    obs = [_obs("node", level="ERROR"), _obs("node", level="DEFAULT")]
    src, _ = _make_source([{"data": obs}])
    result = src.rag_node_stats("qa")
    assert result[0].errors == 1


# ---------------------------------------------------------------------------
# Tokens
# ---------------------------------------------------------------------------

def test_tokens_summed():
    """usage.totalTokens sumados por nombre."""
    obs = [
        _obs("node", usage={"totalTokens": 100}),
        _obs("node", usage={"totalTokens": 200}),
    ]
    src, _ = _make_source([{"data": obs}])
    result = src.rag_node_stats("qa")
    assert result[0].total_tokens == 300


def test_tokens_none_when_absent():
    """obs sin usage → total_tokens=None."""
    obs = [_obs("node")]
    src, _ = _make_source([{"data": obs}])
    result = src.rag_node_stats("qa")
    assert result[0].total_tokens is None


def test_tokens_alternate_key():
    """usage con clave 'total' (en vez de 'totalTokens') → también se suma."""
    obs = [_obs("node", usage={"total": 50})]
    src, _ = _make_source([{"data": obs}])
    result = src.rag_node_stats("qa")
    assert result[0].total_tokens == 50


def test_tokens_total_tokens_none_falls_back_to_total():
    """totalTokens=None con total=42 → usa total (no descarta)."""
    obs = [_obs("node", usage={"totalTokens": None, "total": 42})]
    src, _ = _make_source([{"data": obs}])
    result = src.rag_node_stats("qa")
    assert result[0].total_tokens == 42


# ---------------------------------------------------------------------------
# Resiliencia / defensiva
# ---------------------------------------------------------------------------

def test_defensive_data_non_list():
    """payload con data='oops' → no crash, retorna []."""
    src, _ = _make_source([{"data": "oops"}])
    result = src.rag_node_stats("qa")
    assert result == []


def test_defensive_obs_non_dict():
    """data contiene item que no es dict → se saltea."""
    src, _ = _make_source([{"data": ["not-a-dict", _obs("ok")]}])
    result = src.rag_node_stats("qa")
    assert len(result) == 1
    assert result[0].node == "ok"


def test_defensive_missing_fields():
    """obs sin startTime/endTime/level/usage → no crash."""
    obs = [{"name": "sparse"}]
    src, _ = _make_source([{"data": obs}])
    result = src.rag_node_stats("qa")
    assert len(result) == 1
    assert result[0].calls == 1
    assert result[0].errors == 0
    assert result[0].total_tokens is None


# ---------------------------------------------------------------------------
# Parámetros de request
# ---------------------------------------------------------------------------

def test_from_start_time_param_present():
    """fromStartTime aparece en los params del request."""
    src, fake = _make_source([{"data": []}])
    src.rag_node_stats("qa", since_minutes=60)
    assert len(fake.requests) >= 1
    req_params = fake.requests[0]["params"]
    assert "fromStartTime" in req_params


def test_auth_reused(monkeypatch):
    """Con LANGFUSE_OBS_KEY seteado → auth tuple propagada al factory."""
    monkeypatch.setenv("LANGFUSE_OBS_KEY", "pub-key:sec-key")
    src, fake = _make_source([{"data": []}])
    src.rag_node_stats("qa")
    assert fake.init_auth == ("pub-key", "sec-key")


# ---------------------------------------------------------------------------
# Paginación
# ---------------------------------------------------------------------------

def test_pagination_stops_on_empty_data():
    """Página 1 con 2 obs, página 2 con data vacía → solo 2 obs procesadas."""
    page1 = {"data": [_obs("node"), _obs("node")]}
    page2 = {"data": []}
    src, fake = _make_source([page1, page2])
    result = src.rag_node_stats("qa")
    assert result[0].calls == 2
    # Página 2 fue pedida para verificar que no hay más datos
    assert len(fake.requests) >= 2


# ---------------------------------------------------------------------------
# Ordenamiento y múltiples nombres
# ---------------------------------------------------------------------------

def test_sorted_by_calls_desc():
    """Múltiples nombres con distinta cantidad de calls → ordenados desc."""
    obs = [
        _obs("rare"),
        _obs("frequent"), _obs("frequent"), _obs("frequent"),
        _obs("medium"), _obs("medium"),
    ]
    src, _ = _make_source([{"data": obs}])
    result = src.rag_node_stats("qa")
    calls = [r.calls for r in result]
    assert calls == sorted(calls, reverse=True)
    assert result[0].node == "frequent"


def test_two_names_aggregated_separately():
    """obs divididas entre dos nombres → dos RagNodeStat con counts correctos."""
    obs = [
        _obs("alpha"), _obs("alpha"),
        _obs("beta"),
    ]
    src, _ = _make_source([{"data": obs}])
    result = src.rag_node_stats("qa")
    by_name = {r.node: r for r in result}
    assert by_name["alpha"].calls == 2
    assert by_name["beta"].calls == 1


# ---------------------------------------------------------------------------
# Errores de transporte — degradan a resultado parcial (no propagan)
# ---------------------------------------------------------------------------

def test_rag_node_stats_degrada_a_vacio_en_transport_error(caplog):
    class _RaisingClient:
        def __enter__(self): return self
        def __exit__(self, *exc): return False
        def get(self, *a, **k):
            raise httpx.ConnectTimeout("timed out")

    src = RagPipelineSource(client_factory=lambda base_url, auth: _RaisingClient())
    with caplog.at_level(logging.WARNING):
        result = src.rag_node_stats("dev")

    assert result == []
    assert any(
        r.levelno == logging.WARNING and "inalcanzable" in r.getMessage().lower()
        for r in caplog.records
    )
    assert not any(r.levelno >= logging.ERROR for r in caplog.records)
    assert not any(r.exc_info for r in caplog.records)


def test_rag_node_stats_5xx_propaga():
    """Un HTTPStatusError real (p.ej. 500) NO es un TransportError: debe propagar,
    no degradar a resultado parcial. warn_unreachable() sólo atrapa fallas de transporte."""
    req = httpx.Request("GET", "http://x/api/public/observations")
    err = httpx.HTTPStatusError("500", request=req, response=httpx.Response(500, request=req))

    class _RaisingStatusClient:
        def __enter__(self): return self
        def __exit__(self, *exc): return False
        def get(self, *a, **k):
            return _FakeResponse({}, raise_exc=err)

    src = RagPipelineSource(client_factory=lambda base_url, auth: _RaisingStatusClient())
    with pytest.raises(httpx.HTTPStatusError):
        src.rag_node_stats("dev")


def test_rag_node_stats_partial_en_transport_error_mid_pagina(caplog):
    """Página 1 devuelve datos reales; página 2 falla por transporte a mitad de
    paginación. El agregado parcial de la página 1 debe devolverse (no []), y
    debe quedar un WARNING "inalcanzable" — prueba que el degrade parcial es
    real (usa datos efectivamente acumulados), no un [] accidental."""
    page1 = {"data": [_obs("retriever"), _obs("retriever")]}
    page2_exc = httpx.ConnectError("connection refused")
    src, fake = _make_source([page1, page2_exc])

    with caplog.at_level(logging.WARNING):
        result = src.rag_node_stats("qa")

    assert len(result) == 1
    assert result[0].node == "retriever"
    assert result[0].calls == 2  # sólo lo acumulado en página 1
    assert any(
        r.levelno == logging.WARNING and "inalcanzable" in r.getMessage().lower()
        for r in caplog.records
    )
