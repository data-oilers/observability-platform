# Diseño — Radar del panel: sumar namespaces de plataforma

**Fecha:** 2026-08-25
**Repo:** `observability-platform`
**Estado:** diseño propuesto, pendiente aprobación
**Alcance:** B-1 del roadmap de observabilidad (ver "Fuera de alcance / follow-ups")

## Problema

El radar de salud de workload del panel (`backend/obs_backend/sources/workload.py`)
sólo observa **namespaces de app**: filtra por
`allowed_ns = ENVIRONMENTS[env]["namespaces"]` = `[enterprise-ai, langfuse, airflow]`.

Consecuencia concreta (incidente 2026-08-25): `langfuse-prod` quedó `OutOfSync` porque
el `external-secrets-webhook` de PROD tenía **0 endpoints** — sus 4 pods llevaban 10 días
`Pending` (system-pool de PROD roto desde el 2026-08-14). **El panel no lo mostró**: el ns
`external-secrets` está fuera de su radar. El motor ya sabe detectar `Pending`, shortfalls de
réplica y PVCs en mal estado — sólo no estaba *mirando ahí*.

## Investigación (verificado 2026-08-25)

1. **El dato ya está disponible; sólo se descarta.** `workload.py` hace `list_time_series` de
   las métricas KSM (`kube_pod_status_phase`, `..._waiting_reason`, `..._unschedulable`,
   réplicas de deploy/sts, PVC phase) para **todo el proyecto** y recién ahí filtra
   `if ns not in allowed_ns: continue`. Es decir: las series de `external-secrets`, `kyverno`,
   `kube-system` **ya se traen** de GMP y se tiran. Ampliar `allowed_ns` es el cambio completo;
   **no hay integración nueva** ni costo extra de scrape.

2. **Los namespaces existen y son accionables.** En el cluster de app dev:
   `external-secrets` (76d), `kyverno` (84d), `kube-system` (183d) — todos `Active`. En dev están
   sanos (no habrá falsos positivos); en PROD estarían `Pending` → el panel se prende.

3. **Namespaces de sistema GKE excluidos a propósito.** `gke-managed-*`, `gmp-system`,
   `gmp-public` son propiedad de GKE, baja accionabilidad y meterían ruido. No entran al set.

4. **El motor ya es de bajo ruido.** `workload.py` sólo reporta problemas reales: excluye
   `ContainerCreating`/`PodInitializing`, y para réplicas sólo alarma si `available < desired`
   con la serie `available` presente. En un cluster sano, kube-system no reporta nada.

## Diseño

Introducir un **set fijo** de namespaces de plataforma, observados en **todos los entornos**
además de los de app. Set fijo (no configurable) por decisión explícita: son componentes cuya
caída rompe apps, la postura de seguridad o el cluster.

`backend/obs_backend/config.py`:

```python
# Namespaces de plataforma (infra compartida) observados en TODOS los entornos, además de los
# de app. Set fijo: external-secrets (su webhook caído rompe cualquier apply con ExternalSecret),
# kyverno (admission controller / postura de seguridad), kube-system (kube-dns, metrics-server,
# konnectivity — si caen, cae el cluster). Se excluyen gke-managed-*/gmp-*: GKE-owned, ruido.
PLATFORM_NAMESPACES: frozenset[str] = frozenset({
    "external-secrets",
    "kyverno",
    "kube-system",
})

def watched_namespaces(env: EnvName) -> set[str]:
    """Namespaces que observa el radar de workload: app (por entorno) + plataforma (fijo)."""
    return set(ENVIRONMENTS[env]["namespaces"]) | PLATFORM_NAMESPACES
```

`backend/obs_backend/sources/workload.py` — un renglón:

```python
# antes:  allowed_ns: set[str] = set(ENVIRONMENTS[env]["namespaces"])
# después:
allowed_ns: set[str] = watched_namespaces(env)
```

Sin cambios en `models.py` (`PodIssue`/`ReplicaShortfall`/`PvcIssue` ya llevan `namespace`).
Sin cambios en el frontend: los issues de plataforma aparecen en las mismas listas, rotulados por
su namespace.

## Plan de implementación (TDD)

1. **RED** — en `tests/test_workload.py`, test nuevo: cliente KSM fake que devuelve una serie
   `kube_pod_status_phase` con `namespace=external-secrets, phase=Pending, val=1` → `health()`
   debe devolver un `PodIssue(namespace="external-secrets", problem="Pending")`. Falla hoy
   (se filtra). Segundo test de guardarraíl: un `Pending` en `gke-managed-system` **no** se
   reporta (fija la exclusión).
2. **RED** — en `tests/test_config.py`: `watched_namespaces("dev")` incluye los tres de plataforma
   + los de app; `PLATFORM_NAMESPACES` no incluye `gke-managed-*`.
3. **GREEN** — aplicar los cambios de `config.py` y `workload.py`.
4. Correr toda la suite (`pytest`), verificar verde y que no rompió los tests existentes de
   `workload` (que asumen sólo ns de app — revisar si alguno cuenta issues totales).
5. Code review (silent-failure / scope).

## Fuera de alcance / follow-ups (specs aparte)

> **Verificado el 2026-08-25:** B-2/B-3/B-4 **no** son, como B-1, "ampliar un filtro con datos que
> ya se traen" — las tres están gateadas por infra/IAM. Detalle, prerrequisitos y decisiones en
> [`2026-08-25-radar-roadmap-b2-b3-b4-findings.md`](./2026-08-25-radar-roadmap-b2-b3-b4-findings.md).

- **B-2** — namespace `ecm`: ECM corre en **proyecto+cluster aparte** y aún no está desplegado → diferida.
- **B-3** — source de ArgoCD/GitOps: GMP **no** scrapea ArgoCD → necesita scrape antes del código.
- **B-4** — habilitar PROD: front+back ya lo soportan; falta el grant IAM read-only sobre el proyecto PROD.
- **C** — reducir el ruido en la fuente (exclusion filters en el sink `_Default` +
  alinear alert policies a contenido/métrica). Vive en `itmind-infrastructure`, no en este repo.

## Riesgos

- **kube-system es amplio.** Si un add-on queda temporalmente por debajo de `desired` o `Pending`,
  se reporta. Es señal real (kube-dns/metrics-server caídos importan), pero puede sorprender la
  primera vez. Aceptado; el motor sólo alarma en estados-problema reales.
- **Tests existentes de `workload`** podrían asumir el universo "sólo app". Revisar en paso 4.

## Verificación

- Suite verde.
- Smoke manual post-deploy (opcional): en el panel de dev, la salud de workload sigue sin issues
  (external-secrets/kyverno sanos). La prueba definitiva sería PROD (Fase 3) o un `Pending`
  inducido — no se fuerza.
