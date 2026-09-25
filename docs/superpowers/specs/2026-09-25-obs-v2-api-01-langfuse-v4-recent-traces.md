# OBS-V2-API-01 — Trazas recientes de Langfuse sobre la API v2 (compatibilidad con Langfuse v4)

**Fecha:** 2026-09-25
**Repo:** `observability-platform`
**Estado:** pendiente
**Bloquea:** el cutover de Langfuse DEV a `events_only` (`itmind-infrastructure`, `specs/tenants/macro/7-deploy-langfuse/D-10`)
**Origen:** [Spike: Langfuse v4 en el RAG (DEV)](https://claude.ai/code/artifact/8eeed617-e95e-482b-8c37-f29f520013ec)

## Problema

`backend/obs_backend/sources/langfuse.py::LangfuseSource.recent_traces` arma el panel de trazas recientes con `GET /api/public/traces?limit=N&orderBy=timestamp.desc`. De cada item toma `id`, `name`, `timestamp`, `latency` (segundos), `totalTokens`, `userId` y el score `faithfulness` de `scores`.

Infra está llevando Langfuse a v4 por fases, empezando por DEV. En el cutover (`events_only`), `GET /api/public/traces` **devuelve 404**. El panel de DEV se quedaría sin trazas, y lo mismo pasará en QA y PROD cuando les toque. Mientras tanto, QA y PROD siguen en Langfuse 3.212.0, **donde la API v2 no existe**. La fuente tiene que hablar las dos APIs según el ambiente.

## Reemplazo oficial

Según la [guía de migración de APIs deprecadas](https://langfuse.com/faq/all/deprecated-api-migration) y la definición de la API en `langfuse/langfuse` v4.38.0 (`fern/apis/server/definition/observations.yml`, `scores-v3.yml`, `packages/shared/src/domain/observation-field-groups.ts`):

| Campo del modelo `Trace` | Legacy (`GET /traces`) | v4 |
|---|---|---|
| Lista de trazas | Items de `/traces` | `GET /api/public/v2/observations?isRootObservation=true&fromStartTime=…&toStartTime=…&limit=N&fields=core,basic,metrics,trace_context`: una fila por raíz. Siempre viene ordenado por `startTime` descendente; ya no existe `orderBy` |
| `id` | `id` | `traceId` de la fila raíz |
| `name` | `name` | `traceName` (grupo `trace_context`), con `name` de la raíz como fallback |
| `ts` | `timestamp` | `startTime` de la raíz |
| `latency_ms` | `latency` × 1000 | `latency` de la raíz (grupo `metrics`, en segundos) × 1000 |
| `total_tokens` | `totalTokens` | Ya no es un campo de la traza: se agrega por `traceId`. La guía recomienda la [Metrics API v2](https://langfuse.com/docs/metrics/features/metrics-api#v2) (`GET /api/public/v2/metrics`) para agregados por traza. Alternativa: sumar `usageDetails` de las filas `GENERATION` de esas trazas |
| `faithfulness` | Score en `scores` | `GET /api/public/v3/scores?traceId=<id1>,<id2>,…&name=faithfulness&dataType=NUMERIC` (el filtro `traceId` acepta listas separadas por coma) |
| `user_id` | `userId` | `userId` de la raíz (grupo `basic`) |

Detalles que cambian:

- `fromStartTime` y `toStartTime` son obligatorios en la práctica: la guía pide acotar siempre por tiempo. Una ventana de las últimas 24 horas alcanza para "recientes".
- La paginación pasa a ser por cursor (`meta.cursor`); con `limit` ≤ 1000 no hace falta paginar para este panel.
- Los grupos de `fields` que no se piden **no vienen** (ausentes, no `null`). El parser defensivo actual tiene que tolerarlo.

## Diseño

- Nueva clave por ambiente en `ENVIRONMENTS` (`backend/obs_backend/config.py`): `langfuse_api`, con valor `"legacy"` o `"v2"`. Arranca en `"legacy"` en DEV, QA y PROD, sin cambio de comportamiento.
- `LangfuseSource.recent_traces` elige la implementación según `langfuse_api`. La legacy queda tal cual. La v2 hace hasta 3 requests: la lista de raíces, los scores v3 y los tokens (Metrics v2), y arma el mismo modelo `Trace`. El protocolo `TraceSource` y el frontend no cambian.
- Si falla una de las requests secundarias (scores o tokens), se devuelve la lista igual con esos campos en `None` y se loguea un warning. Nunca se tira el panel entero por un dato secundario.
- DEV pasa a `"v2"` cuando Langfuse DEV está en `dual` (D-08), porque en `dual` la API v2 ya tiene datos. QA y PROD pasan cuando su Langfuse llegue a v4.

## Tests

- Tests de la implementación v2 con respuestas fixture de `/v2/observations`, `/v3/scores` y `/v2/metrics`, incluyendo grupos ausentes, `latency` nulo y score inexistente.
- Test de que con `langfuse_api="legacy"` las requests siguen yendo a `/api/public/traces`, igual que hoy (`backend/tests/test_langfuse.py` sin cambios de comportamiento).
- Test de que un error en scores o tokens devuelve la lista con `None` en esos campos.

## Fuera de alcance

- Otros usos de Langfuse en el panel (no hay otros: `sources/_langfuse.py` es solo auth/cliente).
- Cambiar el modelo `Trace` o el frontend.
- Pasar QA/PROD a `"v2"`: se hace cuando infra migre esos Langfuse.

## Criterios de aceptación

- [ ] Con `langfuse_api="legacy"` en los tres ambientes, el panel se comporta igual que hoy (tests existentes verdes).
- [ ] Con DEV en `"v2"` y Langfuse DEV en `dual`, el panel muestra las mismas trazas recientes que la vista vieja de Langfuse, con nombre, latencia, tokens, faithfulness y usuario.
- [ ] Con Langfuse DEV en `events_only`, el panel de DEV sigue funcionando y los logs del backend no muestran 404.
- [ ] QA y PROD sin cambios mientras su Langfuse siga en v3.
