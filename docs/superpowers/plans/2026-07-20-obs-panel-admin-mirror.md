# Panel Admin Mirror (obs) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a login-less "Panel Admin" tab to `observability-platform` that mirrors, read-only, the RAG admin panel (Supervisión, Reportería, Modelos, Prompts, Identidad) for DEV and QA.

**Architecture:** The obs backend proxies the RAG's admin/analytics/governance HTTP endpoints per environment through a single generic authenticated source (`RagAdminSource`), sending a server-side RAG service-JWT as the `access_token` cookie (the only auth the RAG accepts). Thin obs routes under `/v1/{env}/admin/*` unwrap the RAG `{data,error,meta}` envelope and hand `data` to a new frontend view (`admin.js`) that renders each sub-panel and reuses the existing DEV/QA env switch. The obs frontend stays login-less; the privileged identity lives only in the obs backend.

**Tech Stack:** Python 3.12 + FastAPI + httpx + Pydantic (obs backend); vanilla JS + static HTML/CSS (obs frontend); pytest.

## Global Constraints

- **Read-only only.** obs performs GET against the RAG exclusively. No write routes, no edit controls rendered. Copy verbatim into every backend route: only `@app.get(...)`.
- **DEV and QA only.** PROD is out of scope. Admin routes must reject `env == "prod"` with HTTP 404 even though `ENVIRONMENTS` contains it.
- **Service token never reaches the browser.** The RAG service-JWT is read from env vars server-side and sent only in the obs→RAG request. It must never appear in any obs API response or frontend asset.
- **RAG auth is cookie-only.** The RAG `get_current_user` reads only the `access_token` cookie (`src/infrastructure/api/dependencies.py:51`). obs authenticates by sending header `Cookie: access_token=<jwt>`. There is no Bearer support.
- **Roles come from the RAG DB by `sub`, not the token claim.** The service-user must be seeded in each RAG env DB with the union of roles: `reporteria` + `analistas` (+ `gsi` for Task 9 Identidad).
- **Deploy is gated (not code).** Deployment requires (a) ratification of the isolation-principle exception by the obs charter owner, and (b) provisioning the service-user + secret in `itmind-infrastructure`. Code and tests (mocked) proceed without these; the k8s wiring in Task 10 lands but is not activated until the gate clears.
- **Defensive parsing.** Any unexpected shape from the RAG degrades to empty/`None`, never propagates an exception to the obs client (follow `sources/langfuse.py`).

---

### Task 1: Backend — RAG env config + service-token auth helper

**Files:**
- Modify: `backend/obs_backend/config.py`
- Create: `backend/obs_backend/sources/_rag.py`
- Test: `backend/tests/test_rag_admin_auth.py`

**Interfaces:**
- Produces:
  - `config.ENVIRONMENTS[env]["rag_base_url"]: str`
  - `_rag.rag_token_for(env: str) -> str | None` — reads `RAG_OBS_TOKEN_<ENV>` (e.g. `RAG_OBS_TOKEN_DEV`), strips whitespace, returns `None` if unset/empty.
  - `_rag.default_rag_client_factory(base_url: str, token: str | None) -> httpx.Client` — client with `Cookie: access_token=<token>` header set when token present; timeout from `RAG_OBS_TIMEOUT_SECONDS` (default `15`).

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_rag_admin_auth.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_rag_admin_auth.py -v`
Expected: FAIL — `KeyError: 'rag_base_url'` / `ModuleNotFoundError: obs_backend.sources._rag`.

- [ ] **Step 3: Add `rag_base_url` to each env in `config.py`**

In `backend/obs_backend/config.py`, extend the `EnvConfig` TypedDict and each `ENVIRONMENTS` entry:

```python
class EnvConfig(TypedDict):
    project_id: str
    namespaces: list[str]
    app_namespace: str
    langfuse_url: str
    rag_base_url: str
```

Add to each entry (dev/qa/prod) the matching line:

```python
    # inside "dev":
    "rag_base_url": "https://ia-dev.macro.com.ar",
    # inside "qa":
    "rag_base_url": "https://ia-qa.macro.com.ar",
    # inside "prod":
    "rag_base_url": "https://ia.macro.com.ar",
```

(Confirm the exact QA/PROD hostnames at deploy time; DEV verified from the RAG admin URL in use.)

- [ ] **Step 4: Create `_rag.py`**

```python
# backend/obs_backend/sources/_rag.py
"""Shared utilities for the read-only RAG admin proxy source.

The RAG accepts auth ONLY via the ``access_token`` cookie (no Bearer). obs holds
a service-JWT per environment (seeded service-user with the union of admin roles)
and sends it as that cookie. The token is server-side only and never returned to
the obs frontend.
"""
import os

import httpx


def rag_token_for(env: str) -> str | None:
    """Return the RAG service-JWT for ``env`` from RAG_OBS_TOKEN_<ENV>, or None."""
    raw = os.environ.get(f"RAG_OBS_TOKEN_{env.upper()}", "").strip()
    return raw or None


def default_rag_client_factory(base_url: str, token: str | None) -> httpx.Client:
    timeout = float(os.environ.get("RAG_OBS_TIMEOUT_SECONDS", "15"))
    headers = {"Cookie": f"access_token={token}"} if token else {}
    return httpx.Client(base_url=base_url, headers=headers, timeout=timeout)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_rag_admin_auth.py -v`
Expected: PASS (5 passed).

- [ ] **Step 6: Commit**

```bash
git add backend/obs_backend/config.py backend/obs_backend/sources/_rag.py backend/tests/test_rag_admin_auth.py
git commit -m "feat(backend): RAG env base URLs + service-token auth helper para panel admin mirror"
```

---

### Task 2: Backend — generic `RagAdminSource` proxy + wire into app

**Files:**
- Modify: `backend/obs_backend/sources/base.py`
- Create: `backend/obs_backend/sources/rag_admin.py`
- Modify: `backend/obs_backend/api.py`
- Test: `backend/tests/test_rag_admin_source.py`

**Interfaces:**
- Consumes: `_rag.rag_token_for`, `_rag.default_rag_client_factory`, `ENVIRONMENTS[env]["rag_base_url"]` (Task 1).
- Produces:
  - `base.RagAdminSource` Protocol with `get(self, env: str, path: str, params: dict | None = None) -> object`.
  - `rag_admin.RagAdminSource` concrete impl: GETs `path` on the RAG, unwraps the `{data,error,meta}` envelope, returns `data` (any JSON type). On HTTP error / timeout / bad shape returns `None`.
  - `create_app(..., rag_admin_source: RagAdminSource | None = None)` new optional param.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_rag_admin_source.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_rag_admin_source.py -v`
Expected: FAIL — `ModuleNotFoundError: obs_backend.sources.rag_admin`.

- [ ] **Step 3: Add the Protocol to `base.py`**

Append to `backend/obs_backend/sources/base.py`:

```python
class RagAdminSource(Protocol):
    def get(self, env: str, path: str, params: dict | None = None) -> object:
        """GET a RAG admin/analytics endpoint for ``env`` and return the unwrapped
        ``data`` payload (any JSON type), or None on error/unexpected shape."""
        ...
```

- [ ] **Step 4: Create `rag_admin.py`**

```python
# backend/obs_backend/sources/rag_admin.py
"""Read-only proxy to the RAG admin/analytics/governance HTTP API.

Generic by design: one authenticated GET that unwraps the RAG {data,error,meta}
envelope. Defensive — any error or unexpected shape degrades to None so the
observed system can never break obs.
"""
from typing import Callable

import httpx

from obs_backend.config import ENVIRONMENTS
from obs_backend.sources._rag import default_rag_client_factory, rag_token_for


class RagAdminSource:
    def __init__(
        self,
        client_factory: Callable[[str, str | None], httpx.Client] = default_rag_client_factory,
        token_for: Callable[[str], str | None] = rag_token_for,
    ):
        self._client_factory = client_factory
        self._token_for = token_for

    def get(self, env: str, path: str, params: dict | None = None) -> object:
        base_url = ENVIRONMENTS[env]["rag_base_url"]
        token = self._token_for(env)
        try:
            with self._client_factory(base_url, token) as client:
                resp = client.get(path, params=params or {})
                resp.raise_for_status()
                payload = resp.json()
        except (httpx.HTTPError, ValueError):
            return None
        if not isinstance(payload, dict):
            return None
        return payload.get("data")
```

- [ ] **Step 5: Wire into `create_app`**

In `backend/obs_backend/api.py`:

1. Add the import to the `base` import line:
   `from obs_backend.sources.base import EventsSource, LogSource, MetricsSource, RagAdminSource, RagPipelineSource, TraceSource, WorkloadSource`
2. Add the param to `create_app` signature: `rag_admin_source: RagAdminSource | None = None,`
3. After the `rsource` block, add:

```python
    if rag_admin_source is None:
        from obs_backend.sources.rag_admin import RagAdminSource as _RagAdminSourceImpl
        adminsource: RagAdminSource = _RagAdminSourceImpl()
    else:
        adminsource = rag_admin_source
```

4. Add an env guard helper right after `_check_env`:

```python
    def _check_admin_env(env: str) -> None:
        _check_env(env)
        if env == "prod":
            raise HTTPException(status_code=404, detail="panel admin no disponible en prod")
```

- [ ] **Step 6: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_rag_admin_source.py -v`
Expected: PASS (4 passed).

- [ ] **Step 7: Commit**

```bash
git add backend/obs_backend/sources/base.py backend/obs_backend/sources/rag_admin.py backend/obs_backend/api.py backend/tests/test_rag_admin_source.py
git commit -m "feat(backend): RagAdminSource proxy read-only + wiring en create_app"
```

---

### Task 3: Backend — Supervisión routes

**Files:**
- Modify: `backend/obs_backend/api.py`
- Test: `backend/tests/test_rag_admin_routes.py`

**Interfaces:**
- Consumes: `adminsource.get`, `_check_admin_env` (Task 2).
- Produces obs routes (return the RAG `data` as-is via default response):
  - `GET /v1/{env}/admin/supervision/documents?area&search&page&page_size&sort_by` → RAG `GET /api/v1/admin/governance/documents`
  - `GET /v1/{env}/admin/supervision/documents/{document_id}/chunks` → RAG `GET /api/v1/admin/governance/documents/{document_id}/chunks`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_rag_admin_routes.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_rag_admin_routes.py -v`
Expected: FAIL — 404 on `/v1/dev/admin/supervision/documents` (route not defined).

- [ ] **Step 3: Add the routes in `api.py`**

Add before the static-mount block:

```python
    @app.get("/v1/{env}/admin/supervision/documents")
    def admin_supervision_documents(
        env: str,
        area: str | None = None,
        search: str | None = None,
        page: int = Query(1, ge=1),
        page_size: int = Query(20, ge=1, le=100),
        sort_by: str = "usage_desc",
    ) -> object:
        _check_admin_env(env)
        return adminsource.get(env, "/api/v1/admin/governance/documents", params={
            "area": area, "search": search, "page": page,
            "page_size": page_size, "sort_by": sort_by,
        })

    @app.get("/v1/{env}/admin/supervision/documents/{document_id}/chunks")
    def admin_supervision_chunks(env: str, document_id: int) -> object:
        _check_admin_env(env)
        return adminsource.get(
            env, f"/api/v1/admin/governance/documents/{document_id}/chunks"
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_rag_admin_routes.py -v`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
git add backend/obs_backend/api.py backend/tests/test_rag_admin_routes.py
git commit -m "feat(backend): rutas admin/supervision (documentos + chunks) proxeadas al RAG"
```

---

### Task 4: Frontend — "Panel Admin" tab + view shell + sub-nav + env wiring

**Files:**
- Modify: `frontend/index.html`
- Create: `frontend/admin.js`

**Interfaces:**
- Consumes: obs route `/v1/{env}/admin/supervision/documents` (Task 3).
- Produces: global `showAdminSub(key)` (switches sub-panel); `admin.js` reads `window.currentEnv` and re-renders on env change via a `window.onEnvChange` hook.

- [ ] **Step 1: Add the tab button**

In `frontend/index.html`, inside `<nav class="tabstrip">` after the Pipeline RAG link (line ~340):

```html
    <button class="tab" data-view="admin" onclick="setView('admin')"><span class="ic">⚙</span> Panel Admin <span class="badge">mirror</span></button>
```

- [ ] **Step 2: Add the view section with sub-nav**

In `frontend/index.html`, after the Infra `<section class="view" data-view="infra">…</section>` block, add:

```html
      <!-- ===================== PANEL ADMIN VIEW ===================== -->
      <section class="view" data-view="admin">
        <div class="banner">
          <div class="big"><span class="d"></span> Panel Admin — <b id="adminEnvLabel">DEV</b></div>
          <div class="sub">Mirror read-only del panel de admin del RAG · sólo DEV/QA</div>
        </div>
        <nav class="tabstrip" style="padding:0; background:transparent; border:0; gap:2px;">
          <button class="tab is-active" data-adminsub="supervision" onclick="showAdminSub('supervision')">Supervisión</button>
          <button class="tab" data-adminsub="reporteria" onclick="showAdminSub('reporteria')">Reportería</button>
          <button class="tab" data-adminsub="modelos" onclick="showAdminSub('modelos')">Modelos</button>
          <button class="tab" data-adminsub="prompts" onclick="showAdminSub('prompts')">Prompts</button>
          <button class="tab" data-adminsub="identidad" onclick="showAdminSub('identidad')">Identidad</button>
        </nav>
        <div id="adminBody"><div class="panel" id="panelAdmin"><h3>Cargando…</h3></div></div>
      </section>
```

- [ ] **Step 3: Create `admin.js` with the sub-nav controller**

```javascript
// frontend/admin.js — Panel Admin mirror (read-only). Isolated from app.js.
(function () {
  var current = 'supervision';

  function el(id) { return document.getElementById(id); }
  function env() { return window.currentEnv || 'qa'; }

  function adminFetch(path) {
    return fetch('/v1/' + env() + '/admin' + path).then(function (r) {
      if (!r.ok) throw new Error('HTTP ' + r.status);
      return r.json();
    });
  }
  window.adminFetch = adminFetch;

  var RENDERERS = {};            // filled by Task 5+ (renderSupervision, etc.)
  window.registerAdminRenderer = function (key, fn) { RENDERERS[key] = fn; };

  function render() {
    var lbl = el('adminEnvLabel'); if (lbl) lbl.textContent = env().toUpperCase();
    var body = el('adminBody'); if (!body) return;
    var fn = RENDERERS[current];
    if (!fn) { body.innerHTML = '<div class="panel"><h3>' + current + '</h3><p>Próximamente.</p></div>'; return; }
    body.innerHTML = '<div class="panel" id="panelAdmin"><h3>Cargando…</h3></div>';
    fn(body, env());
  }

  window.showAdminSub = function (key) {
    current = key;
    document.querySelectorAll('[data-adminsub]').forEach(function (b) {
      b.classList.toggle('is-active', b.getAttribute('data-adminsub') === key);
    });
    render();
  };

  // Re-render when the env switch fires (app.js sets window.currentEnv then calls this).
  window.onEnvChange = (function (prev) {
    return function () { if (prev) prev(); render(); };
  })(window.onEnvChange);

  // Re-render when the admin tab becomes visible.
  window.onViewChange = (function (prev) {
    return function (view) { if (prev) prev(view); if (view === 'admin') render(); };
  })(window.onViewChange);
})();
```

- [ ] **Step 4: Load `admin.js` and expose the hooks in `index.html`**

Add after the `app.js` script tag (line ~701):

```html
<script src="admin.js" defer></script>
```

In the inline `<script>` (line ~604), inside the existing `setView` function add a call at the end: `if (window.onViewChange) window.onViewChange(view);`. Inside the existing env-switch handler, after `currentEnv` is updated and `envGen` bumped, add: `window.currentEnv = currentEnv; if (window.onEnvChange) window.onEnvChange();`. (Locate `setView` and the env button handler by searching the inline script; both already exist.)

- [ ] **Step 5: Manual verification**

Run backend with the frontend mounted:
`cd backend && OBS_FRONTEND_DIR=../frontend python -m uvicorn obs_backend.main:app --port 8099`
Open `http://localhost:8099`, click **Panel Admin**. Expected: the view shows, sub-nav switches highlight, each non-Supervisión sub shows "Próximamente", env label tracks the DEV/QA switch.

- [ ] **Step 6: Commit**

```bash
git add frontend/index.html frontend/admin.js
git commit -m "feat(frontend): tab Panel Admin + shell/sub-nav + wiring de entorno (admin.js)"
```

---

### Task 5: Frontend — Supervisión render (doc list + detail)

**Files:**
- Modify: `frontend/admin.js`
- Test: manual (static frontend).

**Interfaces:**
- Consumes: `/v1/{env}/admin/supervision/documents`, `/v1/{env}/admin/supervision/documents/{id}/chunks` (Task 3); `window.adminFetch`, `window.registerAdminRenderer` (Task 4).

- [ ] **Step 1: Add the Supervisión renderer in `admin.js`**

Before the final `})();` add:

```javascript
  function esc(s) { return String(s == null ? '' : s).replace(/[&<>]/g, function (c) {
    return { '&': '&amp;', '<': '&lt;', '>': '&gt;' }[c]; }); }

  function renderSupervision(body, e) {
    adminFetch('/supervision/documents?page=1&page_size=50').then(function (data) {
      var items = (data && data.items) || [];
      var rows = items.map(function (d) {
        return '<tr data-docid="' + esc(d.document_id) + '">' +
          '<td>' + esc(d.document_name) + '</td>' +
          '<td>' + esc(d.area) + ' ' + esc(d.version || '') + '</td>' +
          '<td class="mono">' + esc(d.usage_count) + ' usos</td>' +
          '<td class="mono">' + esc(d.unique_users) + ' users</td>' +
          '<td class="mono"><span class="tag ok">+' + esc(d.positive_count) + '</span> ' +
          '<span class="tag crit">−' + esc(d.negative_count) + '</span></td>' +
          '</tr>';
      }).join('');
      body.innerHTML =
        '<div class="panel"><h3>Documentos <span class="badge">' + items.length + '</span></h3>' +
        '<div class="tablewrap"><table><thead><tr><th>Documento</th><th>Área</th>' +
        '<th>Usos</th><th>Users</th><th>Feedback</th></tr></thead><tbody>' +
        (rows || '<tr><td colspan="5">Sin documentos.</td></tr>') +
        '</tbody></table></div></div>' +
        '<div class="panel" id="panelAdminDetail"><h3>Chunks</h3>' +
        '<p class="mono" style="color:var(--text-dim)">Elegí un documento.</p></div>';
      body.querySelectorAll('tr[data-docid]').forEach(function (tr) {
        tr.style.cursor = 'pointer';
        tr.onclick = function () { renderChunks(tr.getAttribute('data-docid')); };
      });
    }).catch(function () {
      body.innerHTML = '<div class="panel"><h3>Documentos <span class="err-chip">error</span></h3></div>';
    });
  }

  function renderChunks(docId) {
    var detail = document.getElementById('panelAdminDetail'); if (!detail) return;
    detail.innerHTML = '<h3>Chunks</h3><p>Cargando…</p>';
    adminFetch('/supervision/documents/' + encodeURIComponent(docId) + '/chunks').then(function (data) {
      var chunks = (data && data.items) || (Array.isArray(data) ? data : []);
      detail.innerHTML = '<h3>Chunks <span class="badge">' + chunks.length + '</span></h3>' +
        '<div class="tablewrap"><table><tbody>' +
        (chunks.map(function (c) {
          return '<tr><td class="mono">' + esc(c.chunk_id || c.id || '') + '</td><td>' +
            esc((c.text || c.content || '').slice(0, 160)) + '…</td></tr>';
        }).join('') || '<tr><td>Sin chunks.</td></tr>') +
        '</tbody></table></div>';
    }).catch(function () { detail.innerHTML = '<h3>Chunks <span class="err-chip">error</span></h3>'; });
  }

  registerAdminRenderer('supervision', renderSupervision);
```

- [ ] **Step 2: Manual verification against a live RAG (or a stub)**

With `RAG_OBS_TOKEN_DEV=<jwt>` set and the backend running (Task 4 command), open Panel Admin → Supervisión. Expected: the document table renders with names/areas/usos/users matching the real DEV panel; clicking a row loads its chunks. If no token/live RAG is available, verify against a stub by pointing `rag_base_url` at a local fixture server.
Confirm the exact chunk field names (`chunk_id`/`text` vs alternatives) against the live RAG response and adjust the `esc(...)` accessors if they differ — the defensive `||` fallbacks already cover the common variants.

- [ ] **Step 3: Commit**

```bash
git add frontend/admin.js
git commit -m "feat(frontend): render Supervisión (lista de documentos + detalle de chunks)"
```

---

### Task 6: Reportería sub-panel (backend route + frontend render)

**Files:**
- Modify: `backend/obs_backend/api.py`, `frontend/admin.js`
- Test: add cases to `backend/tests/test_rag_admin_routes.py`

**Interfaces:**
- Consumes: `adminsource.get`, `_check_admin_env`, `registerAdminRenderer`, `adminFetch`.
- Produces: `GET /v1/{env}/admin/reporteria?date_from&date_to` → RAG `GET /api/v1/analytics/dashboard/executive` (role `reporteria`).

- [ ] **Step 1: Write the failing test (append to `test_rag_admin_routes.py`)**

```python
def test_reporteria_forwards_dates():
    fake = FakeAdmin()
    _client(fake).get("/v1/dev/admin/reporteria",
                      params={"date_from": "2026-07-01", "date_to": "2026-07-20"})
    assert fake.calls[0][1] == "/api/v1/analytics/dashboard/executive"
    assert fake.calls[0][2]["date_from"] == "2026-07-01"
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd backend && python -m pytest tests/test_rag_admin_routes.py::test_reporteria_forwards_dates -v`
Expected: FAIL — 404 (route missing).

- [ ] **Step 3: Add the route in `api.py`** (before static mount)

```python
    @app.get("/v1/{env}/admin/reporteria")
    def admin_reporteria(env: str, date_from: str, date_to: str) -> object:
        _check_admin_env(env)
        return adminsource.get(env, "/api/v1/analytics/dashboard/executive",
                               params={"date_from": date_from, "date_to": date_to})
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd backend && python -m pytest tests/test_rag_admin_routes.py::test_reporteria_forwards_dates -v`
Expected: PASS.

- [ ] **Step 5: Add the frontend renderer (in `admin.js`, before `})();`)**

```javascript
  function renderReporteria(body, e) {
    var to = new Date().toISOString().slice(0, 10);
    var from = new Date(Date.now() - 30 * 864e5).toISOString().slice(0, 10);
    adminFetch('/reporteria?date_from=' + from + '&date_to=' + to).then(function (data) {
      body.innerHTML = '<div class="panel"><h3>Reportería ejecutiva</h3>' +
        '<pre class="mono" style="white-space:pre-wrap;overflow:auto">' +
        esc(JSON.stringify(data, null, 2)) + '</pre></div>';
    }).catch(function () {
      body.innerHTML = '<div class="panel"><h3>Reportería <span class="err-chip">error</span></h3></div>';
    });
  }
  registerAdminRenderer('reporteria', renderReporteria);
```

(A structured KPI layout can replace the `<pre>` in a follow-up; this task delivers the data faithfully.)

- [ ] **Step 6: Commit**

```bash
git add backend/obs_backend/api.py backend/tests/test_rag_admin_routes.py frontend/admin.js
git commit -m "feat: sub-panel Reportería (dashboard ejecutivo) en el mirror admin"
```

---

### Task 7: Modelos sub-panel (backend route + frontend render)

**Files:**
- Modify: `backend/obs_backend/api.py`, `frontend/admin.js`
- Test: add case to `backend/tests/test_rag_admin_routes.py`

**Interfaces:**
- Produces: `GET /v1/{env}/admin/modelos` → RAG `GET /api/v1/admin/model-routing` (role `analistas`).

- [ ] **Step 1: Write the failing test (append)**

```python
def test_modelos_route():
    fake = FakeAdmin()
    _client(fake).get("/v1/qa/admin/modelos")
    assert fake.calls[0][1] == "/api/v1/admin/model-routing"
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd backend && python -m pytest tests/test_rag_admin_routes.py::test_modelos_route -v`
Expected: FAIL — 404.

- [ ] **Step 3: Add the route in `api.py`**

```python
    @app.get("/v1/{env}/admin/modelos")
    def admin_modelos(env: str) -> object:
        _check_admin_env(env)
        return adminsource.get(env, "/api/v1/admin/model-routing")
```

Note: the RAG model-routing router mounts at `/admin/model-routing` under the v1 include prefix (`main.py:280` includes it `prefix="/api/v1"`). Confirm the resolved path at impl time; if it resolves without `/api/v1`, use `/admin/model-routing`.

- [ ] **Step 4: Run to verify it passes**

Run: `cd backend && python -m pytest tests/test_rag_admin_routes.py::test_modelos_route -v`
Expected: PASS.

- [ ] **Step 5: Add the frontend renderer (in `admin.js`)**

```javascript
  function renderModelos(body, e) {
    adminFetch('/modelos').then(function (data) {
      var rows = (Array.isArray(data) ? data : (data && data.items) || []);
      body.innerHTML = '<div class="panel"><h3>Model routing <span class="badge">' + rows.length + '</span></h3>' +
        '<div class="tablewrap"><table><thead><tr><th>Nodo</th><th>Modelo</th>' +
        '<th>Temp</th><th>Max tokens</th></tr></thead><tbody>' +
        (rows.map(function (r) {
          return '<tr><td class="mono">' + esc(r.node || r.node_name) + '</td><td>' +
            esc(r.model) + '</td><td class="mono">' + esc(r.temperature) +
            '</td><td class="mono">' + esc(r.max_tokens) + '</td></tr>';
        }).join('') || '<tr><td colspan="4">Sin filas.</td></tr>') +
        '</tbody></table></div></div>';
    }).catch(function () {
      body.innerHTML = '<div class="panel"><h3>Modelos <span class="err-chip">error</span></h3></div>';
    });
  }
  registerAdminRenderer('modelos', renderModelos);
```

Confirm the row field names (`node`/`model`/`temperature`/`max_tokens`) against the live RAG response; the `||` fallbacks cover the common variants.

- [ ] **Step 6: Commit**

```bash
git add backend/obs_backend/api.py backend/tests/test_rag_admin_routes.py frontend/admin.js
git commit -m "feat: sub-panel Modelos (model routing, read-only) en el mirror admin"
```

---

### Task 8: Prompts sub-panel (backend route + frontend render)

**Files:**
- Modify: `backend/obs_backend/api.py`, `frontend/admin.js`
- Test: add case to `backend/tests/test_rag_admin_routes.py`

**Interfaces:**
- Produces: `GET /v1/{env}/admin/prompts` → RAG `GET /api/v1/admin/prompts` (role `analistas`).

- [ ] **Step 1: Write the failing test (append)**

```python
def test_prompts_route():
    fake = FakeAdmin()
    _client(fake).get("/v1/dev/admin/prompts")
    assert fake.calls[0][1] == "/api/v1/admin/prompts"
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd backend && python -m pytest tests/test_rag_admin_routes.py::test_prompts_route -v`
Expected: FAIL — 404.

- [ ] **Step 3: Add the route in `api.py`**

```python
    @app.get("/v1/{env}/admin/prompts")
    def admin_prompts(env: str) -> object:
        _check_admin_env(env)
        return adminsource.get(env, "/api/v1/admin/prompts")
```

(Same path-prefix caveat as Task 7 — confirm the resolved RAG path.)

- [ ] **Step 4: Run to verify it passes**

Run: `cd backend && python -m pytest tests/test_rag_admin_routes.py::test_prompts_route -v`
Expected: PASS.

- [ ] **Step 5: Add the frontend renderer (in `admin.js`)**

```javascript
  function renderPrompts(body, e) {
    adminFetch('/prompts').then(function (data) {
      var rows = (Array.isArray(data) ? data : (data && data.items) || []);
      body.innerHTML = '<div class="panel"><h3>Prompts <span class="badge">' + rows.length + '</span></h3>' +
        rows.map(function (p) {
          return '<details><summary class="mono">' + esc(p.name || p.tier || p.id) + '</summary>' +
            '<pre class="mono" style="white-space:pre-wrap;overflow:auto">' +
            esc(p.content || p.template || JSON.stringify(p, null, 2)) + '</pre></details>';
        }).join('') + '</div>';
    }).catch(function () {
      body.innerHTML = '<div class="panel"><h3>Prompts <span class="err-chip">error</span></h3></div>';
    });
  }
  registerAdminRenderer('prompts', renderPrompts);
```

Confirm field names against the live RAG response; `||` fallbacks cover variants.

- [ ] **Step 6: Commit**

```bash
git add backend/obs_backend/api.py backend/tests/test_rag_admin_routes.py frontend/admin.js
git commit -m "feat: sub-panel Prompts (governance, read-only) en el mirror admin"
```

---

### Task 9: Identidad sub-panel (backend route + frontend render) — highest privilege

**Files:**
- Modify: `backend/obs_backend/api.py`, `frontend/admin.js`
- Test: add case to `backend/tests/test_rag_admin_routes.py`

**Interfaces:**
- Produces: `GET /v1/{env}/admin/identidad` → RAG settings endpoint (role `gsi`).

**Pre-step (resolve the endpoint):** grep the RAG for the settings/identity endpoint that the panel's "Identidad" screen consumes:
`grep -rniE "identity|identidad|branding|/settings|app_name|logo" src/api/routes src/infrastructure/api/v1 --include="*.py"`.
Set `RAG_IDENTIDAD_PATH` below to the resolved path (e.g. `/api/v1/admin/settings`). If Identidad requires `gsi` that the team decided to exclude, skip this task and leave the sub-nav button rendering "Próximamente" (Task 4 already does this) — that is a valid v1 endpoint.

- [ ] **Step 1: Write the failing test (append)**

```python
def test_identidad_route():
    fake = FakeAdmin()
    _client(fake).get("/v1/qa/admin/identidad")
    assert fake.calls[0][1] == "<RAG_IDENTIDAD_PATH>"   # replace with resolved path
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd backend && python -m pytest tests/test_rag_admin_routes.py::test_identidad_route -v`
Expected: FAIL — 404.

- [ ] **Step 3: Add the route in `api.py`**

```python
    @app.get("/v1/{env}/admin/identidad")
    def admin_identidad(env: str) -> object:
        _check_admin_env(env)
        return adminsource.get(env, "<RAG_IDENTIDAD_PATH>")   # resolved in pre-step
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd backend && python -m pytest tests/test_rag_admin_routes.py::test_identidad_route -v`
Expected: PASS.

- [ ] **Step 5: Add the frontend renderer (in `admin.js`)**

```javascript
  function renderIdentidad(body, e) {
    adminFetch('/identidad').then(function (data) {
      body.innerHTML = '<div class="panel"><h3>Identidad</h3>' +
        '<pre class="mono" style="white-space:pre-wrap;overflow:auto">' +
        esc(JSON.stringify(data, null, 2)) + '</pre></div>';
    }).catch(function () {
      body.innerHTML = '<div class="panel"><h3>Identidad <span class="err-chip">error</span></h3></div>';
    });
  }
  registerAdminRenderer('identidad', renderIdentidad);
```

- [ ] **Step 6: Commit**

```bash
git add backend/obs_backend/api.py backend/tests/test_rag_admin_routes.py frontend/admin.js
git commit -m "feat: sub-panel Identidad (settings, read-only) en el mirror admin"
```

---

### Task 10: Deploy wiring + gate documentation (no secret activation)

**Files:**
- Modify: `k8s/deployment.yaml`
- Modify: `README.md`
- Test: `kubectl apply --dry-run=client -f k8s/deployment.yaml` (or `kustomize build`/helm equivalent as used by the repo).

**Interfaces:** none (config/docs only).

- [ ] **Step 1: Add env vars to the deployment (referencing a Secret, not inlining values)**

In `k8s/deployment.yaml`, add to the container `env:` list:

```yaml
        - name: RAG_OBS_TOKEN_DEV
          valueFrom:
            secretKeyRef: { name: obs-rag-tokens, key: dev, optional: true }
        - name: RAG_OBS_TOKEN_QA
          valueFrom:
            secretKeyRef: { name: obs-rag-tokens, key: qa, optional: true }
```

`optional: true` so the pod still boots before the secret exists (routes return `data: null` until the token is provisioned — the frontend shows the error chip, no crash).

- [ ] **Step 2: Document the gate in `README.md`**

Under the components list, add a subsection:

```markdown
### Panel Admin (mirror read-only)

Tab que reproduce el panel de admin del RAG (Supervisión, Reportería, Modelos,
Prompts, Identidad) para DEV/QA, sin login. El backend proxea la API del RAG con
un **service-JWT read-only por entorno** (`RAG_OBS_TOKEN_DEV` / `_QA`, secret
`obs-rag-tokens`).

> ⚠️ **Excepción al principio rector.** Este feature hace que obs sostenga una
> identidad contra lo observado. NO desplegar sin: (1) ratificación del owner del
> charter de obs; (2) service-user sembrado con roles `reporteria`+`analistas`
> (+`gsi` si Identidad) en el DB del RAG DEV/QA; (3) secret `obs-rag-tokens`
> provisionado en `itmind-infrastructure` (`fast/tenants/macro/observability/`).
> Renovación del JWT: es un token firmado con la clave del cluster (patrón
> eval-fixture); definir TTL y rotación en infra.
```

- [ ] **Step 3: Validate the manifest**

Run: `kubectl apply --dry-run=client -f k8s/deployment.yaml`
Expected: `deployment.apps/… configured (dry run)` with no schema error.

- [ ] **Step 4: Commit**

```bash
git add k8s/deployment.yaml README.md
git commit -m "chore(deploy): env/secret refs para service-token del panel admin + gate de aislamiento documentado"
```

---

## Self-Review

**Spec coverage:**
- Login-less mirror tab → Tasks 4–9 ✅
- Supervisión → Tasks 3, 5 ✅ · Reportería → Task 6 ✅ · Modelos → Task 7 ✅ · Prompts → Task 8 ✅ · Identidad → Task 9 ✅
- Read-only (GET-only, no edit controls) → enforced across all backend/frontend tasks + Global Constraints ✅
- DEV/QA only, PROD rejected → `_check_admin_env` (Task 2), tested (Task 3) ✅
- Server-side token, never to browser → Tasks 1–2, Task 10 secret refs ✅
- Isolation exception ratification + infra provisioning gate → Task 10 docs + Global Constraints ✅
- Defensive parsing → `RagAdminSource.get` returns None on error (Task 2), frontend `.catch` chips ✅
- Testing (mocked RAG) → Tasks 1–3, 6–9 ✅

**Placeholder scan:** The only intentional deferrals are the RAG hostname confirmation (Task 1 Step 3), the model-routing/prompts resolved-path caveat (Tasks 7–8), and the Identidad endpoint resolution (Task 9 pre-step, with an explicit grep command and a valid skip path). These are live-system confirmations, not unwritten code — every task ships complete, runnable code and tests.

**Type consistency:** `RagAdminSource.get(env, path, params=None) -> object` is defined in Task 2 and consumed identically in Tasks 3, 6–9. Frontend `registerAdminRenderer(key, fn)` / `adminFetch(path)` defined in Task 4, consumed in Tasks 5–9. `_check_admin_env` defined in Task 2, used in Tasks 3, 6–9. Consistent.
