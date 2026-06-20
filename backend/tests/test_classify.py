import json

from obs_backend.classify import classify_line


def test_json_structlog_request():
    raw = json.dumps({
        "severity": "info", "event": "http_request", "path": "/api/v1/chat",
        "status": 200, "duration_ms": 2840, "request_id": "a4f1-9c", "user_id": "85763",
    })
    ev = classify_line(raw, "enterprise-ai-api-7c9f")
    assert ev.severity == "INFO"
    assert ev.path == "/api/v1/chat"
    assert ev.status == 200
    assert ev.duration_ms == 2840
    assert ev.request_id == "a4f1-9c"
    assert ev.user_id == "85763"
    assert ev.pod == "api-7c9f"  # se recorta el prefijo enterprise-ai-


def test_uvicorn_access_log_5xx():
    raw = '127.0.0.1 - "POST /api/v1/chat HTTP/1.1" 502'
    ev = classify_line(raw, "enterprise-ai-api-b3d1")
    assert ev.path == "/api/v1/chat"
    assert ev.status == 502


def test_ragas_openai_error_is_warning_not_error():
    raw = "Traceback (most recent call last): OpenAIError: api_key client option must be set"
    ev = classify_line(raw, "enterprise-ai-api-7c9f")
    assert ev.severity == "WARNING"  # ruido conocido, no pinta el semáforo


def test_no_active_span_is_warning():
    raw = "[warning] Context error: No active span in current context"
    ev = classify_line(raw, "enterprise-ai-api-b3d1")
    assert ev.severity == "WARNING"


def test_plain_error_is_error():
    raw = "[error] unhandled exception in handler"
    ev = classify_line(raw, "enterprise-ai-api-7c9f")
    assert ev.severity == "ERROR"


# Fix C: rama consola key=value → parsea todos los campos relevantes.
def test_console_kv_parses_all_fields():
    raw = "path=/x status_code=200 duration_ms=12 request_id=r1 user_id=u1"
    ev = classify_line(raw, "pod-1")
    assert ev.path == "/x"
    assert ev.status == 200
    assert ev.duration_ms == 12.0
    assert ev.request_id == "r1"
    assert ev.user_id == "u1"


# Fix C: ancla fix A — status_code=0 y duration_ms=0 deben preservarse como 0.
def test_console_kv_zero_values_preserved():
    raw = "status_code=0 duration_ms=0"
    ev = classify_line(raw, "pod-1")
    assert ev.status == 0
    assert ev.duration_ms == 0.0


# Fix C: JSON malformado → cae a texto, no rompe.
def test_malformed_json_falls_back_to_text():
    raw = "{not json"
    ev = classify_line(raw, "pod-1")
    assert ev.message == "{not json"
    assert ev.severity == "INFO"  # sin señal de error, default


# Fix C: línea plana [critical] → severity CRITICAL.
def test_plain_critical_line():
    raw = "[critical] sistema caído"
    ev = classify_line(raw, "pod-1")
    assert ev.severity == "CRITICAL"


# Fix C: línea con status_code=503 sin nivel de log → escala a ERROR por 5xx.
def test_console_5xx_status_escalates_to_error():
    raw = "status_code=503 path=/health"
    ev = classify_line(raw, "pod-1")
    assert ev.status == 503
    assert ev.severity == "ERROR"
