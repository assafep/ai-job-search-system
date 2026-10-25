# Cloud Run deployment

Build and push the image defined by `deployment/docker/Dockerfile`, then deploy
the Application Service as a Cloud Run service. Supply the Drive credentials and
API token through Secret Manager rather than putting them in the image. Prefer
refresh-token credentials for a long-running service:

```bash
gcloud run deploy job-search-application-service \
  --image YOUR_REGISTRY/job-search-application-service:latest \
  --port 8080 \
  --set-env-vars REPOSITORY_BACKEND=google,GOOGLE_INTERVIEW_JOURNAL_URL=https://docs.google.com/document/d/...,GOOGLE_ARTIFACT_ROOT_URL=https://drive.google.com/drive/folders/... \
  --set-secrets GOOGLE_ACCESS_TOKEN=google-drive-access-token:latest,APPLICATION_SERVICE_TOKEN=application-service-token:latest
```

For refresh-based authentication, replace the Drive access-token binding with
Secret Manager bindings for `GOOGLE_REFRESH_TOKEN`, `GOOGLE_CLIENT_ID`, and
`GOOGLE_CLIENT_SECRET`; the repository refreshes the access token on demand.

When application folders are grouped below a Drive-root directory, also set
`GOOGLE_APPLICATION_ROOT_PATH=Applications` is the default parent; set it only to choose another root-relative parent
layout.

Workflow execution is separate from this service. A Cloud Run Job or worker
process can invoke the provider-neutral loop:

```bash
AGENT_PROVIDER=my_provider:provider \
python3 -m services.worker_main --poll-seconds 30
```

Use `--once` for a scheduled drain, or invoke
`python3 -m services.workflow_runtime` with captured agent results for a
deterministic one-workload replay. Provide durable `WORKLOAD_REGISTRY_PATH`
and `APPROVAL_REGISTRY_PATH` to the worker execution environment; for Cloud
Run Jobs, use a durable mounted store or an external registry adapter before
scaling beyond one worker. Do not expose the persistence service publicly
without setting `APPLICATION_SERVICE_TOKEN`.
