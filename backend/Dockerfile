FROM python:3.12-slim AS base
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1

# IMPORTANTE: buildear desde la RAÍZ del repo (context = ".") porque la imagen
# incluye backend + frontend:
#   docker build -f backend/Dockerfile -t <IMAGE> .
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/obs_backend ./obs_backend
COPY frontend ./frontend

# Sirve el frontend vía StaticFiles cuando OBS_FRONTEND_DIR está seteado.
ENV OBS_FRONTEND_DIR=/app/frontend

# Kyverno: non-root, sin privilegios. UID alto fijo.
# El runAsNonRoot real lo enforza el securityContext del manifest (k8s/deployment.yaml);
# este USER es backstop.
RUN useradd -u 65532 -m nonroot
USER 65532:65532

EXPOSE 8800
CMD ["uvicorn", "obs_backend.main:app", "--host", "0.0.0.0", "--port", "8800"]
