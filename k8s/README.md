# Deploy manual — plataforma de observabilidad (Fase 1.B-2)

Deploy **manual con `kubectl`** (NO ArgoCD por ahora) al cluster del proyecto **`itmind-macro-auto-0`**, namespace **`observability`**. El backend FastAPI sirve también el frontend (StaticFiles), así que es **una sola imagen / un Service / una URL**.

> Todo lo de abajo lo corrés vos. La imagen lee GCP por **Workload Identity** (SA `obs-backend@itmind-macro-auto-0`, grants read-only de Fase 0) — sin keys.

## 0. Pre-requisitos a confirmar / completar (placeholders)

**Todo manual** (sin Jenkins, sin ArgoCD): `docker build/push` a mano + `kubectl apply`.

| Dónde | Placeholder | Qué poner |
|---|---|---|
| `deployment.yaml` | `image:` (default `.../observability/obs-backend:0.1.0`) | confirmá el repo AR y el tag (paso 1) |
| `service.yaml` | `__CLUSTER_IP__` (opcional) | IP fija libre del service CIDR, para estabilizar `obs.hs.internal` |
| `networkpolicy.yaml` | selector del `from` | namespace/labels del subnet-router de Tailscale |
| — | cluster + `kubectl` context | el cluster de auto-0 con Workload Identity habilitado |

## 1. Build + push de la imagen (manual)

Si todavía no existe el repo de Artifact Registry en auto-0, crealo una vez:
```bash
gcloud artifacts repositories create observability \
  --repository-format=docker --location=us-east1 \
  --project=itmind-macro-auto-0 \
  --description="Imágenes de la plataforma de observabilidad"
gcloud auth configure-docker us-east1-docker.pkg.dev
```

**Build desde la RAÍZ del repo** (la imagen incluye `backend/` + `frontend/`) y push.
**IMPORTANTE:** los nodos GKE son `linux/amd64`; en Mac (arm64) hay que **cross-buildear** o el pod da `no match for platform`:
```bash
cd ~/Documents/desarrollos/observability-platform
TAG=0.1.0   # o $(git rev-parse --short HEAD); usá tag inmutable, no 'latest'
IMAGE=us-east1-docker.pkg.dev/itmind-macro-auto-0/observability/obs-backend:$TAG
docker buildx build --platform linux/amd64 --provenance=false \
  -f backend/Dockerfile -t "$IMAGE" --push .
```
(`--provenance=false` evita el manifest-list con attestations que algunos containerd rechazan; `--push` sube directo.)

- Smoke local opcional antes de pushear:
  `docker run --rm -p 8800:8800 "$IMAGE"` → `curl -s localhost:8800/healthz` = `{"status":"ok"}` (las rutas `/v1/...` necesitan WI en cluster).
- Si el `TAG` ≠ `0.1.0`, actualizá `image:` en `deployment.yaml`.
- **Pull permission:** el SA de los nodos del cluster (o quien haga el pull) necesita `roles/artifactregistry.reader` sobre el repo, si no → `ImagePullBackOff`:
  ```bash
  gcloud artifacts repositories add-iam-policy-binding observability \
    --location=us-east1 --project=itmind-macro-auto-0 \
    --member="serviceAccount:<NODE_SA>@itmind-macro-auto-0.iam.gserviceaccount.com" \
    --role="roles/artifactregistry.reader"
  ```

## 2. Secret de Langfuse (read-only) — opcional al inicio

El Deployment monta `LANGFUSE_OBS_KEY` con `optional: true`, así que **podés deployar antes** de tener la key (Langfuse/RAG quedan sin auth hasta entonces; el resto de la API anda). Cuando generes la key read-only en Langfuse (formato `public:secret`):

```bash
# opción simple/manual: secret de k8s directo
kubectl -n observability create secret generic langfuse-obs \
  --from-literal=LANGFUSE_OBS_KEY='pk-...:sk-...'
# luego: kubectl -n observability rollout restart deploy/obs-backend
```

(Si más adelante usan External Secrets Operator, este secret se reemplaza por un `ExternalSecret` que lo sincroniza desde Secret Manager — el valor `langfuse-obs-readonly` ya existe en auto-0, hoy vacío.)

## 3. Apply

```bash
kubectl apply -f k8s/namespace.yaml
kubectl apply -f k8s/serviceaccount.yaml
kubectl apply -f k8s/service.yaml
kubectl apply -f k8s/networkpolicy.yaml
kubectl apply -f k8s/deployment.yaml
kubectl -n observability rollout status deploy/obs-backend
```

Verificación:
```bash
kubectl -n observability get pods,svc
kubectl -n observability logs deploy/obs-backend | tail
# WI ok si los endpoints /v1/qa/... devuelven datos reales (no 500/permiso).
```

## 4. Acceso `obs.hs.internal` (Tailscale / Headscale)

Una vez que el Service tenga IP:
```bash
kubectl -n observability get svc obs-backend -o jsonpath='{.spec.clusterIP}'
```
Con esa IP, en la config de Headscale (repo `itmind-infrastructure`):
1. **`TS_ROUTES`** del subnet-router: agregar la IP (o el service CIDR) a las rutas anunciadas.
2. **`extra_records`**: `obs.hs.internal` → esa ClusterIP.
3. **ACL** (`acls.hujson`): regla que permita al grupo de operadores (`group:obs` / el grupo que uses) llegar a esa IP:8800.
4. Reconciliar Headscale y aprobar la ruta.

Resultado: `http://obs.hs.internal` desde cualquier device en la VPN → el dashboard (y `/rag-pipeline.html`).

> Decime y te escribo los cambios concretos de Headscale apenas tengas la ClusterIP y me confirmes la ubicación/formato de la config (acls.hujson + extra_records).

## Notas
- `backend/.dockerignore` quedó obsoleto (el build ahora usa `.dockerignore` de la raíz); es inocuo.
- `securityContext` cumple Kyverno (non-root, no-priv-escalation, drop ALL, readOnlyRootFS + emptyDir `/tmp`). Si el namespace necesita un `LimitRange`, los `requests/limits` del Deployment ya están seteados.
- PROD se agrega en Fase 3 (hoy el deploy y los grants son QA).
