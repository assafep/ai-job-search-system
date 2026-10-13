# Kubernetes deployment

Build and push the image from the repository's Dockerfile, replace
`YOUR_REGISTRY/...` in `persistence-api.yaml`, then create the non-secret
configuration and secret objects:

```bash
kubectl create configmap job-search-google \
  --from-literal=interview-journal-url='https://docs.google.com/document/d/...' \
  --from-literal=artifact-root-url='https://drive.google.com/drive/folders/...' \
  --from-literal=application-root-path='applications'

Omit `application-root-path` to retain the existing direct-root application
folder layout.

kubectl create secret generic job-search-google \
  --from-literal=access-token="$GOOGLE_ACCESS_TOKEN" \
  --from-literal=persistence-api-token="$PERSISTENCE_API_TOKEN"

kubectl apply -f deployment/kubernetes/persistence-api.yaml
```

For renewable authentication, provide `refresh-token`, `client-id`, and
`client-secret` keys in the same secret instead of `access-token`; the manifest
marks both credential modes optional and the repository selects the configured
mode.

The manifest deploys only the persistence API. A scheduler should invoke
`python3 -m services.workflow_runtime` as a short-lived job, using the same
API URL and registry configuration; application state remains in Google Drive.
