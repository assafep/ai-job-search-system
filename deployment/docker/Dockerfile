# Pin both the Python patch release and the immutable multi-platform image digest.
FROM python:3.12.12-slim-bookworm@sha256:593bd06efe90efa80dc4eee3948be7c0fde4134606dd40d8dd8dbcade98e669c

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PORT=8080 HOME=/tmp
WORKDIR /app
COPY deployment/docker/requirements.lock /app/requirements.lock
RUN pip install --no-cache-dir --require-hashes --only-binary=:all: -r requirements.lock \
    && pip check

# Explicit sources only: no credentials, runtime state, or provisioning files.
COPY application_service/ application_service/
COPY services/ services/
COPY stages/ stages/
COPY agents/ agents/
COPY schemas/ schemas/
COPY adapters/ adapters/
COPY config/pipelines.json config/discovery-sources.json config/automation-policy.yaml config/
COPY openapi.yaml ./
USER 10001:10001
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:' + os.environ.get('PORT', '8080') + '/ready', timeout=4).read()"
CMD ["sh", "-c", "exec python -m application_service.main --host 0.0.0.0 --port \"${PORT:-8080}\""]
