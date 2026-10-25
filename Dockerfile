# UNVERIFIED in the build environment (no Docker available there). See docs/TESTING.md.
FROM node:22-slim AS dashboard
WORKDIR /dash
COPY crro-dashboard/package.json ./
RUN npm install --no-audit --no-fund
COPY crro-dashboard/ ./
RUN npm run build

FROM python:3.12-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY crro/ ./crro/
COPY scripts/ ./scripts/
COPY api.py ./
COPY --from=dashboard /dash/dist ./dashboard
RUN python3 scripts/train_model.py --out /app/outputs/model && useradd -r -u 10001 crro && chown -R crro /app
USER crro
ENV CRRO_MODEL_DIR=/app/outputs/model CRRO_DASHBOARD_DIR=/app/dashboard
EXPOSE 8000
HEALTHCHECK CMD python3 -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health/ready').status==200 else 1)"
CMD ["uvicorn", "api:app", "--host", "0.0.0.0", "--port", "8000"]
