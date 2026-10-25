# Docker deployment

Run the Application Service locally with:

```bash
docker compose up --build application-service
```

With no `config/runtime.json`, the API uses the in-memory backend. After local
onboarding creates that file, Compose leaves `REPOSITORY_BACKEND` unset so the
API automatically selects the configured Google Drive journal and artifact
root. Set `REPOSITORY_BACKEND=memory` explicitly for an isolated local run, or
set it to `google` when supplying all Drive settings through environment
variables. Use refresh-token credentials for long-running services. Credentials
are passed as environment variables and are not copied into the image.

The build excludes local credentials, runtime configuration, Git metadata, and
IDE/test cache files through the repository `.dockerignore`. The image also
contains a `/ready`-based Docker health check; `/health` is reserved for
process liveness.
