FROM python:3.12-slim-bookworm AS base
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app
COPY requirements.txt requirements-dev.txt /app/

FROM base AS source
COPY gdtq /app/gdtq
COPY adapters /app/adapters
COPY agents /app/agents
COPY config/gdtq-routing.json /app/config/gdtq-routing.json
COPY schemas /app/schemas
RUN mkdir -p /app/var

FROM base AS development
RUN pip install --no-cache-dir -r requirements-dev.txt
COPY --from=source /app /app

FROM base AS runtime
RUN pip install --no-cache-dir -r requirements.txt
COPY --from=source /app /app
CMD ["python","-m","gdtq.cli","run","--database","/app/var/ajss-gdtq.sqlite3"]
