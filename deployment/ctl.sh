#!/bin/sh
# Provision and deployment entrypoint. Run from anywhere:
#   ./deployment/ctl.sh plan
#   ./deployment/ctl.sh onboard
#   ./deployment/ctl.sh drive
#   ./deployment/ctl.sh up
#   ./deployment/ctl.sh drive-login
#   ./deployment/ctl.sh chatgpt
#   ./deployment/ctl.sh connect
#   ./deployment/ctl.sh codex
#   ./deployment/ctl.sh codex-login
#   ./deployment/ctl.sh runner
#   ./deployment/ctl.sh start|stop|status
#   ./deployment/ctl.sh vercel-start|vercel-stop|vercel-status
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
  codex "Complete the AJSS integration ChatGPT. if browser control is available, use my signed-in ChatGPT session to add ${ajss-application-service} with OAuth and dynamic client registration. If I have grok installed register the MCP also there by calling 'grok mcp add --transport http ajss ${ajss-application-service}'. The token can be found at .onboarding/chatgpt/authentication.json. Complete all supported setup steps, pausing only for authentication or consent that requires me. Keep credentials out of output. Verify the ChatGPT connection by querying queue counts."
}

if [ "${1:-}" = codex ]; then
  register_codex
  exit $?
fi

if [ -n "${AJSS_INSIDE_PROVISION:-}" ]; then
  cd "$repo"
  if [ "${1:-}" = runner ]; then
    exec python3 -m provisioning.docker_runner
  fi
  if [ "${1:-}" = codex-login ]; then
    exec codex login --device-auth
  fi
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

host_uid=$(id -u)
host_gid=$(id -g)
gcloud_config=${CLOUDSDK_CONFIG:-${HOME:-/root}/.config/gcloud}
mkdir -p "$gcloud_config"

token_env=
if [ -n "${VERCEL_TOKEN:-}" ]; then
  token_env='-e VERCEL_TOKEN'
fi

# Mount only this repository. Host networking keeps browser-login callbacks
# on localhost.
connection_marker=
browser_dir=
browser_pid=
cleanup() {
  if [ -n "$browser_pid" ]; then
    kill "$browser_pid" 2>/dev/null || true
    wait "$browser_pid" 2>/dev/null || true
  fi
  if [ -n "$browser_dir" ]; then
    rm -rf -- "$browser_dir"
  fi
  if [ -n "$connection_marker" ]; then
    rm -f -- "$connection_marker"
  fi
}
trap cleanup 0
trap 'exit 130' INT
trap 'exit 143' TERM
if [ "${1:-}" = up ] || [ "${1:-}" = chatgpt ] || [ "${1:-}" = connect ]; then
  connection_marker=$(mktemp)
fi

relay_browser_requests() {
  while [ -d "$browser_dir" ]; do
    if [ -f "$browser_dir/request.url" ]; then
      browser_url=
      IFS= read -r browser_url < "$browser_dir/request.url" || true
      rm -f -- "$browser_dir/request.url"
      browser_opened=0
      case "$browser_url" in
        https://accounts.google.com/o/oauth2/v2/auth\?*|https://accounts.google.com/o/oauth2/auth\?*)
          if command -v xdg-open >/dev/null 2>&1; then
            xdg-open "$browser_url" >/dev/null 2>&1 && browser_opened=1
          elif command -v gio >/dev/null 2>&1; then
            gio open "$browser_url" >/dev/null 2>&1 && browser_opened=1
          elif command -v open >/dev/null 2>&1; then
            open "$browser_url" >/dev/null 2>&1 && browser_opened=1
          fi
          ;;
      esac
      printf '%s\n' "$browser_opened" > "$browser_dir/result.tmp"
      mv -- "$browser_dir/result.tmp" "$browser_dir/result"
    fi
    sleep 0.1
  done
}
case "${1:-}" in
  up|drive|drive-login|onboard)
    mkdir -p "$repo/.onboarding"
    browser_dir=$(mktemp -d "$repo/.onboarding/.browser-XXXXXX")
    relay_browser_requests &
    browser_pid=$!
    ;;
esac
run_provision() {
  provision_command=${1:-}
  provision_codex_home="$repo/.onboarding/codex"
  set -- "$image" "$script_dir/ctl.sh" "$@"
  if [ "$provision_command" = up ] || [ "$provision_command" = runner ] || [ "$provision_command" = start ] || [ "$provision_command" = stop ] || [ "$provision_command" = status ]; then
    docker_endpoint=${DOCKER_HOST:-$(docker context inspect --format '{{.Endpoints.docker.Host}}')}
    case "$docker_endpoint" in
      unix://*) docker_socket=${docker_endpoint#unix://} ;;
      *) echo 'Runner provisioning requires a local Unix Docker socket.' >&2; return 1 ;;
    esac
    socket_gid=$(stat -c '%g' "$docker_socket")
    set -- --group-add "$socket_gid" \
      -v "$docker_socket:/run/ajss-docker.sock" \
      -e AJSS_DOCKER_SOCKET=/run/ajss-docker.sock "$@"
  fi
  if [ "$provision_command" = up ] || [ "$provision_command" = runner ] || [ "$provision_command" = onboard ]; then
    if command -v grok >/dev/null 2>&1; then
      grok_binary=$(readlink -f "$(command -v grok)")
      set -- -v "$grok_binary:/opt/ajss-host-cli/grok:ro" "$@"
    fi
    host_codex_config=${CODEX_HOME:-${HOME:-/root}/.codex}
    if [ -f "$host_codex_config/auth.json" ]; then
      provision_codex_home=/home/ajss/.codex
      set -- -e "AJSS_HOST_CODEX_HOME=$host_codex_config" \
        -v "$host_codex_config:/home/ajss/.codex" "$@"
    fi
    if [ -f "${HOME:-/root}/.grok/auth.json" ]; then
      set -- -e "AJSS_HOST_GROK_HOME=${HOME:-/root}/.grok" \
        -v "${HOME:-/root}/.grok:/home/ajss/.grok" "$@"
    fi
  fi
  docker run --rm $interactive \
    --user "$host_uid:$host_gid" \
    --tmpfs "/home/ajss:rw,mode=700,uid=$host_uid,gid=$host_gid" \
    --network host \
    -e AJSS_INSIDE_PROVISION=1 \
    -e AJSS_BROWSER_RELAY_DIR="$browser_dir" \
    -e ONBOARD_PYTHON=python3 \
    -e AGENT_PROVIDER \
    -e CODING_AGENT \
    -e CODING_AGENT_MODEL \
    -e CODEX_MODEL \
    -e CODEX_HOME="$provision_codex_home" \
    -e PATH=/opt/ajss-host-cli:/opt/google-cloud-sdk/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
    -e HOME=/home/ajss \
    -e CLOUDSDK_CONFIG=/home/ajss/.config/gcloud \
    $token_env \
    -v "$repo:$repo" \
    -v "$gcloud_config:/home/ajss/.config/gcloud" \
    -w "$repo" \
    "$@"
}
run_status=0
run_provision "$@" || run_status=$?

# Runner provisioning can still be blocked after a successful connection stage.
# Register only when that stage wrote a verified MCP handoff, and never inside Docker.
if [ "${1:-}" = up ] || [ "${1:-}" = chatgpt ] || [ "${1:-}" = connect ]; then
  if [ "$repo/.onboarding/chatgpt/verified.json" -nt "$connection_marker" ] && command -v codex >/dev/null 2>&1; then
    register_codex || echo 'Codex registration failed; rerun ./deployment/ctl.sh codex.' >&2
  fi
fi
exit "$run_status"
