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

## Pinned production image on Vercel

`Dockerfile.vercel` selects Vercel's Container preset instead of Python entrypoint
inference. It matches `deployment/docker/Dockerfile`; keep both files identical.
The provisioning upload includes the Dockerfile and lockfile, and explicitly
selects the Container framework. All HTTP paths route directly to the server;
the old `/api/index.py` rewrite is removed. Cron still calls the existing route.
The Container runtime is currently a Vercel beta.

Python 3.12.12 is pinned by image digest. Every Python dependency, including
transitive dependencies, is pinned with SHA-256 hashes in `requirements.lock`.
Installation requires hashes and binary wheels, then runs `pip check`.
The service runs as UID 10001, uses an explicit source allowlist, and accepts
`PORT` (default 8080). Both Vercel configurations set `PORT=8080` for ingress; preserve that value
in project environment overrides. Vercel defaults to port 80 without it.
Supply production credentials through the project's environment settings.

Build and smoke-test without production credentials:

```bash
docker build -f Dockerfile.vercel -t ajss-application-service:pinned .
docker run --rm -p 8080:8080 -e REPOSITORY_BACKEND=memory ajss-application-service:pinned
curl --fail http://localhost:8080/health
curl --fail http://localhost:8080/ready
```

The Python-runtime Queue subscriber is not registered by this container.
Provisioning rejects `workflow_execution.vercel_enabled=true` until that
subscriber is migrated. Do not directly deploy the container over a project
with hosted queue execution enabled. The native ChatGPT workflows stay on
their existing host.

Regenerate the lock deliberately after reviewing dependency updates:

```bash
docker run --rm --user "$(id -u):$(id -g)" -e HOME=/tmp -v "$PWD:/src" -w /src \
  python:3.12.12-slim-bookworm@sha256:593bd06efe90efa80dc4eee3948be7c0fde4134606dd40d8dd8dbcade98e669c \
  sh -c 'python -m venv /tmp/lock-tools && /tmp/lock-tools/bin/pip install pip-tools==7.5.1 && /tmp/lock-tools/bin/pip-compile --generate-hashes --strip-extras --output-file=deployment/docker/requirements.lock requirements.txt'
```

Rebuild and verify the image after updating the lock or base digest. This pins
build inputs; it does not certify that dependencies have no vulnerabilities.
