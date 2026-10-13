# Deployment

The repository includes a Docker image for the persistence API, a Kubernetes
manifest, and Cloud Run deployment guidance. The persistence API is a long-lived
service; workflow execution is a separate short-lived runtime invocation:

```text
adapter event → workload registry → services.workflow_runtime → persistence API
```

Keep Google credentials and `PERSISTENCE_API_TOKEN` in the deployment platform's
secret manager. The Kubernetes and Cloud Run materials do not move or modify
the user's Drive files during deployment.

Workflow workers should configure durable `WORKLOAD_REGISTRY_PATH` and,
when human approvals are enabled, `APPROVAL_REGISTRY_PATH` on a persistent
volume or equivalent durable storage. These JSON registries contain scheduler
and approval bookkeeping only; application state remains in the persistence
API and Interviews Journal.

The provider-neutral worker process can be launched with:

```bash
python3 -m services.worker_main --poll-seconds 30
```

The default local provider runs the logged-in Codex CLI and is intended for a
single-user workstation. For a cloud deployment, set an explicit
deployment-owned provider through `AGENT_PROVIDER`; do not assume a local
Codex login exists in the cloud environment. Any provider must return the
structured results required by the selected agent schemas. For a scheduler or
smoke test, add `--once`.
