FROM python:3.12-slim AS base
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY obs_backend ./obs_backend
COPY frontend ./frontend

# Serve the frontend via StaticFiles when OBS_FRONTEND_DIR is set.
ENV OBS_FRONTEND_DIR=/app/frontend

# Kyverno: non-root, sin privilegios. UID alto fijo.
# El runAsNonRoot real lo enforza el securityContext del manifest en Fase 1.B;
# este USER es backstop en caso de que el manifest no lo especifique.
RUN useradd -u 65532 -m nonroot
USER 65532:65532

EXPOSE 8800
CMD ["uvicorn", "obs_backend.main:app", "--host", "0.0.0.0", "--port", "8800"]
