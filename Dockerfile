# The frontend is built in a separate stage so the runtime images never contain
# npm, source maps, parquet input, generated CSVs, or developer credentials.
FROM node:22-alpine AS frontend-build

WORKDIR /src/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --ignore-scripts
COPY frontend/ ./
RUN npm run build

FROM python:3.11-slim AS pipeline

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY analytics/ analytics/
COPY agent_tools/ agent_tools/
COPY agent_orchestrator/ agent_orchestrator/
COPY money_graph/ money_graph/

RUN useradd --create-home --uid 10001 app \
    && mkdir -p /app/data /app/out /app/state \
    && chown -R app:app /app
USER app

VOLUME ["/app/data", "/app/out"]
ENTRYPOINT ["python", "-m", "money_graph"]
CMD ["--data", "/app/data", "--out", "/app/out", "--check-repro"]

FROM nginx:1.27-alpine AS ui

COPY --from=frontend-build /src/frontend/dist/ /usr/share/nginx/html/
COPY deploy/nginx.conf /etc/nginx/conf.d/default.conf
RUN mkdir -p /usr/share/nginx/html/out

EXPOSE 80
HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=3 \
    CMD wget -q -O - http://127.0.0.1/healthz >/dev/null || exit 1
