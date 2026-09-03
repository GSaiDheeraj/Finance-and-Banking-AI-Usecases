# Single container: the FastAPI backend, which also serves the vanilla-JS UI in web/ as
# static files (see credit_risk/api/main.py's StaticFiles mount). Streamlit is a separate,
# unchanged local dev tool and is deliberately not part of this image.
#
# Postgres and Redis are external/managed — nothing DB-related is baked into this image;
# connection details (PG_*, REDIS_*, LLM_*) are supplied at runtime via the environment
# (see .env.example, and k8s/configmap.yaml + k8s/secret.yaml for the Kubernetes target).
FROM python:3.12-slim

WORKDIR /app

# Install dependencies first so this layer is cached across code-only changes.
COPY pyproject.toml requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY credit_risk/ ./credit_risk/
COPY web/ ./web/

ENV PYTHONPATH=/app \
    PYTHONUNBUFFERED=1

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/health', timeout=3).status == 200 else 1)"

CMD ["uvicorn", "credit_risk.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
