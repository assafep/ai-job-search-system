# Tools for provisioning and deployment. The repo is mounted at /src; this image
# does not copy it.
#
#   ./deployment/ctl.sh plan --setup vercel
FROM python:3.12-slim-bookworm

ARG NODE_VERSION=22.23.4
ARG GCLOUD_VERSION=590.0.1
ARG VERCEL_VERSION=65.2.0

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl postgresql-client \
    && arch="$(dpkg --print-architecture)" \
    && case "$arch" in \
         amd64) node_arch=x64; gcloud_arch=x86_64 ;; \
         arm64) node_arch=arm64; gcloud_arch=arm ;; \
         *) echo "unsupported architecture: $arch" >&2; exit 1 ;; \
       esac \
    && curl -fsSL "https://nodejs.org/dist/v${NODE_VERSION}/node-v${NODE_VERSION}-linux-${node_arch}.tar.gz" \
         | tar -xz -C /usr/local --strip-components=1 \
    && npm install -g "vercel@${VERCEL_VERSION}" \
    && rm -rf /usr/local/lib/node_modules/npm \
              /usr/local/lib/node_modules/corepack \
              /root/.npm \
              /usr/local/bin/npm /usr/local/bin/npx /usr/local/bin/corepack \
    && curl -fsSL "https://dl.google.com/dl/cloudsdk/channels/rapid/downloads/google-cloud-cli-${GCLOUD_VERSION}-linux-${gcloud_arch}.tar.gz" \
         | tar -xz -C /opt \
    && /opt/google-cloud-sdk/install.sh --quiet --usage-reporting=false --path-update=false --command-completion=false \
    && rm -rf /opt/google-cloud-sdk/platform/gsutil \
              /opt/google-cloud-sdk/platform/bq \
              /opt/google-cloud-sdk/.install/.backup \
              /opt/google-cloud-sdk/bin/gsutil \
              /opt/google-cloud-sdk/bin/bq \
              /root/.config/gcloud \
    && apt-get purge -y curl \
    && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/*

# Schema initialization imports psycopg. The repo is not copied into this image.
RUN pip install --no-cache-dir 'psycopg[binary]==3.2.12' \
    'mcp==1.26.0' 'PyYAML==6.0.3' 'jsonschema==4.26.0'

ENV PATH="/opt/google-cloud-sdk/bin:${PATH}" \
    CLOUDSDK_CORE_DISABLE_PROMPTS=1 \
    PYTHONUNBUFFERED=1

WORKDIR /src
