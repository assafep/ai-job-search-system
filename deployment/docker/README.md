# Docker deployment — Application Service compatibility image

`deployment/docker/Dockerfile` and `docker-compose.yml` currently build and run the **legacy Application Service API**. They do **not** copy `gdtq/`, mount a durable SQLite index, or launch `python -m gdtq.cli run`. Do not use `docker compose up` expecting a GDTQ worker.

The API remains useful for onboarding, old MCP clients and compatibility operations. Start it in isolation (do not simultaneously enable its legacy writers on the same document):

```bash
docker compose up --build application-service
```

GDTQ's initial CLI runs as one standalone Python process with configured OAuth and a persistent local DB volume. Before building a production GDTQ image, add GDTQ packages/typed schemas/config, a dedicated entrypoint and data volume, single-writer fencing, health/readiness, OAuth scopes and live Google Docs integration tests.

See [GDTQ setup](../../gdtq/README.md) and [deployment operator notes](../README.md).
