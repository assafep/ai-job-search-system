#!/bin/sh
# Provision and deployment entrypoint. Run from anywhere:
#   ./deployment/ctl.sh plan
#   ./deployment/ctl.sh drive
#   ./deployment/ctl.sh up
#   ./deployment/ctl.sh drive-login
#   ./deployment/ctl.sh chatgpt
#   ./deployment/ctl.sh connect
#   ./deployment/ctl.sh codex
#   ./deployment/ctl.sh mcp-stdio
# Answers live in deployment/defaults.json. Flags override that file.
# The image is built on first use and rebuilt when provision.Dockerfile changes.
set -eu

image=ajss-provision
script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
repo=$(CDPATH= cd -- "$script_dir/.." && pwd)
dockerfile="$repo/deployment/docker/provision.Dockerfile"

# Codex configuration belongs to the host user, not the provisioning container.
register_codex() {
  if ! command -v codex >/dev/null 2>&1; then
    echo 'Codex CLI is unavailable. Install it, then run ./deployment/ctl.sh codex.' >&2
    return 1
  fi
  codex mcp add ajss-application-service -- "$script_dir/ctl.sh" mcp-stdio
}

if [ "${1:-}" = codex ]; then
  register_codex
  exit $?
fi

if [ -n "${AJSS_INSIDE_PROVISION:-}" ]; then
  cd "$repo"
  if [ "${1:-}" = mcp-stdio ]; then
    exec python3 -m application_service.mcp_adapter
  fi
  exec python3 -m provisioning "$@"
fi

if ! command -v docker >/dev/null 2>&1; then
  echo 'docker is required to run provisioning.' >&2
  exit 1
fi

dockerfile_sha=$(sha256sum "$dockerfile" | awk 'NR==1 { print $1 }')
built_sha=$(docker image inspect -f '{{index .Config.Labels "ajss.dockerfile.sha256"}}' "$image" 2>/dev/null || true)
if [ "$built_sha" != "$dockerfile_sha" ]; then
  docker build \
    --label "ajss.dockerfile.sha256=$dockerfile_sha" \
    -f "$dockerfile" -t "$image" "$repo/deployment/docker" >&2
fi

interactive=
if [ "${1:-}" = mcp-stdio ]; then
  interactive=-i
elif [ -t 0 ] && [ -t 1 ]; then
  interactive=-it
fi

gcloud_config=${CLOUDSDK_CONFIG:-${HOME:-/root}/.config/gcloud}
mkdir -p "$gcloud_config"

token_env=
if [ -n "${VERCEL_TOKEN:-}" ]; then
  token_env='-e VERCEL_TOKEN'
fi

# The parent directory is mounted at the same path so the sibling
# auto-signup-vercel checkout stays next to this repo. Host networking keeps
# browser-login callbacks on localhost.
connection_marker=
if [ "${1:-}" = up ] || [ "${1:-}" = chatgpt ] || [ "${1:-}" = connect ]; then
  connection_marker=$(mktemp)
  trap 'rm -f "$connection_marker"' 0
fi
run_status=0
docker run --rm $interactive \
  --network host \
  -e AJSS_INSIDE_PROVISION=1 \
  -e ONBOARD_PYTHON=python3 \
  -e HOME=/root \
  $token_env \
  -v "$repo/..:$repo/.." \
  -v "$gcloud_config:/root/.config/gcloud" \
  -w "$repo" \
  "$image" \
  "$script_dir/ctl.sh" "$@" || run_status=$?

# Runner provisioning can still be blocked after a successful connection stage.
# Register only when that stage wrote a verified MCP handoff, and never inside Docker.
if [ "${1:-}" = up ] || [ "${1:-}" = chatgpt ] || [ "${1:-}" = connect ]; then
  if [ "$repo/.onboarding/chatgpt/verified.json" -nt "$connection_marker" ] && command -v codex >/dev/null 2>&1; then
    register_codex || echo 'Codex registration failed; rerun ./deployment/ctl.sh codex.' >&2
  fi
fi
exit "$run_status"
