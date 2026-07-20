# Diseño — Tab "Panel Admin" en obs (mirror read-only, sin login)

**Fecha:** 2026-07-20
**Repo:** `observability-platform`
**Estado:** diseño aprobado, pendiente escribir plan de implementación

## Problema

Hoy, para ver el **Panel de Admin del RAG** (Supervisión, Reportería, Identidad,
Modelos, Prompts) hay que entrar a `ia-dev.macro.com.ar` / `ia-qa…` y loguearse
con una cuenta Azure AD que tenga los roles adecuados. No hay forma de mirar
"lo que ve alguien con todos los roles y permisos" en DEV y QA desde un solo
lugar y **sin login**.

Se quiere: dentro de `observability-platform` (que ya es el panel único
read-only con switch DEV/QA/PROD), una vista **login-less** que reproduzca el
Panel de Admin del RAG **como lo vería un usuario con todos los roles**, para
DEV y QA.

## Restricciones descubiertas (investigación)

1. **El panel admin del RAG no es iframe-able cross-origin.** En el RAG,
   `frontend/src/middleware.ts` sólo marca `/embedded` y `/chat` como
   embeddables; `/analytics/*` (donde vive el Panel Admin) sale con
   `X-Frame-Options: DENY` + `frame-ancestors 'none'`
   (`src/infrastructure/api/middleware/security_headers.py`). Además las cookies
   de auth son `SameSite=lax` por default → en un iframe cross-site ni viajarían.
   → **Un embed real queda descartado** (obligaría a degradar la seguridad del RAG).

2. **La data del panel vive detrás de endpoints agregados de admin**, sin
   filtrado ABAC por usuario. Mapa de roles (verificado en el RAG):

   | Sub-panel   | Endpoint(s) RAG                                             | Rol       |
   |-------------|------------------------------------------------------------|-----------|
   | Supervisión | `/api/v1/analytics/*` · `/api/v1/admin/governance/*`        | `reporteria` |
   | Reportería  | `/api/v1/analytics/dashboard/*`                            | `reporteria` |
   | Modelos     | `/api/v1/admin/model-routing`                              | `analistas` |
   | Prompts     | `/api/v1/admin/prompts`                                    | `analistas` |
   | Identidad   | settings admin                                            | `gsi` (confirmar en impl) |

   Como son agregados de admin (no ABAC per-user), **"todos los roles y permisos"
   para estos paneles ≡ una identidad de servicio con la unión de esos roles**,
   no un usuario impersonado por documento.

3. **El backend de obs hoy no toca el RAG.** Sólo alcanza Cloud
   Logging/Monitoring + Langfuse por entorno (`backend/obs_backend/config.py`,
   `sources/`). Este feature agrega una fuente nueva.

4. **Principio rector de obs:** *"la plataforma observa; nunca comparte identidad
   ni recursos con lo que observa"* (`README.md`). Este feature introduce una
   **identidad de servicio de obs contra el RAG** → roce explícito con el
   charter, que debe ratificarse (ver §Decisión de aislamiento).

## Enfoque elegido — Opción C (reproducir la data read-only en obs)

Descartadas: embed en iframe (rompe seguridad del RAG) y deep-link con login
(el requisito es **sin login**).

obs **proxea y renderiza** la data del Panel Admin del RAG por entorno, como una
tab más del panel único, login-less igual que el resto de obs.

### Frontend (`frontend/`)

- Nueva tab **"Panel Admin"** en el tabstrip de `index.html`, siguiendo el patrón
  de las tabs actuales (App / Infra / Pipeline RAG). Puede ser una view interna
  (`<section class="view" data-view="admin">`) o una página propia estilo
  `rag-pipeline.html` — decisión menor a resolver en el plan; se prefiere view
  interna para reusar el switch de entorno y el shell.
- Sub-nav que espeja el panel real: **ANALÍTICAS** (Reportería, Supervisión) ·
  **CONFIGURACIÓN** (Identidad, Modelos, Prompts).
- **Render read-only.** Aunque el panel real permite editar Modelos/Prompts, obs
  sólo muestra. No se renderizan controles de edición.
- Reusa el switch **DEV/QA** existente (`currentEnv`, fetch a `/v1/<env>/…` con
  el patrón de generación `envGen` de `app.js`). **PROD queda fuera** de este v1.

### Backend (`backend/obs_backend/`)

- Nuevo módulo source (p.ej. `sources/rag_admin.py`) con un `Protocol` en
  `sources/base.py`, consistente con las fuentes existentes.
- Nuevas rutas en `api.py`, mismo patrón `/v1/{env}/…`:
  - `GET /v1/{env}/admin/supervision`
  - `GET /v1/{env}/admin/supervision/documents/{document_id}/chunks`
  - `GET /v1/{env}/admin/reporteria`
  - `GET /v1/{env}/admin/model-routing`
  - `GET /v1/{env}/admin/prompts`
  - `GET /v1/{env}/admin/identidad`
  (nombres/superficie exactos a fijar en el plan según lo que consuma cada view).
- El source llama a la API del RAG (base URL del RAG por entorno, a agregar en
  `config.py` junto al `ENVIRONMENTS` actual) con un **token de servicio
  read-only por entorno**, guardado server-side (env var / secret), **nunca
  expuesto al browser**.
- Sólo GET. Sin rutas de escritura. Las mutaciones son imposibles desde obs por
  construcción.
- Normaliza la respuesta del RAG a modelos Pydantic en `models.py`
  (`response_model=…`) como el resto de las rutas.
- `_check_env` sigue rechazando entornos desconocidos; PROD puede quedar
  habilitado en config pero sin token → devuelve error controlado (o se excluye
  explícitamente en las rutas admin).

### Modelo de identidad (a ratificar)

- obs sostiene **un service-user read-only del RAG por entorno** (DEV, QA) con la
  unión mínima de roles necesaria: **`reporteria` + `analistas`** (+ `gsi` sólo si
  el sub-panel Identidad lo exige — a confirmar en impl).
- Provisión: (1) visto de quien manda en el charter de obs por el roce con el
  principio rector; (2) sembrar el service-user con esos roles en el DB del RAG
  DEV/QA (los roles se leen del DB por `sub`, no del claim del token); (3) el
  secret/token vive en `itmind-infrastructure`
  (`fast/tenants/macro/observability/`), igual que el resto de las credenciales
  de obs.
- Mitigación del roce: read-only, roles acotados (no god-account gsi salvo lo
  imprescindible), token server-side, obs incapaz de mutar.

## Decisión de aislamiento (bloqueante para deploy)

Este feature **contradice el principio rector de obs**. No se despliega sin:
1. Ratificación explícita del roce por el owner del charter de obs.
2. Provisión del/los service-user(s) y secret(s) en `itmind-infrastructure`.

La implementación (código) puede escribirse y testearse con mocks sin esperar
esto; el deploy no.

## Fuera de alcance (v1)

- **PROD** (sólo DEV y QA).
- **Edición** de cualquier setting (Modelos/Prompts/Identidad) — obs es read-only.
- **Embed iframe** del panel vivo (descartado por §Restricciones).
- **Deep-link con login** (descartado por requisito "sin login").

## Secuencia sugerida (incrementos)

Para minimizar el privilegio inicial y entregar valor temprano, se sugiere fasear
(a detallar en el plan de implementación):

1. **Supervisión + Reportería** (sólo rol `reporteria`) — la vista del screenshot
   original + dashboards.
2. **Modelos + Prompts** (suma rol `analistas`).
3. **Identidad** (suma rol `gsi` si aplica) — el incremento de mayor privilegio,
   al final.

Alcance total aprobado = los 5 sub-paneles; la secuencia es sólo orden de entrega.

## Testing

- Backend: tests por source/ruta que mockean la API del RAG (patrón de los
  `backend/tests/test_*.py` existentes). Cubrir: mapeo de respuesta, manejo de
  error/timeout del RAG, rechazo de entorno desconocido, ausencia de token.
- Frontend: estático; validación manual del render por sub-panel y del switch
  DEV/QA.

## Riesgos

- **Deriva de contrato:** si el RAG cambia sus endpoints de admin, obs se rompe.
  Mitigación: normalización en un solo source + tests de mapeo.
- **Roce de charter:** ya tratado en §Decisión de aislamiento.
- **Confusión de origen de dato:** el usuario de obs podría creer que está en el
  panel real. Mitigación: etiquetar la tab como "mirror read-only" y mostrar
  el entorno (DEV/QA) de forma prominente (ya lo hace el shell de obs).
