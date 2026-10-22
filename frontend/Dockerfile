# Built from the repo root (see docker-compose.yml) so it can reuse the
# backend's CA handling for TLS-intercepting filters (backend/certs/README.md).

FROM node:22-slim AS build

ENV NODE_EXTRA_CA_CERTS=/etc/ssl/certs/ca-certificates.crt

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    openssl \
    && rm -rf /var/lib/apt/lists/*
COPY backend/certs/ /usr/local/share/ca-certificates/extra/
RUN update-ca-certificates
COPY backend/netfree-certs.sh /usr/local/bin/netfree-certs
RUN chmod +x /usr/local/bin/netfree-certs

ARG NETFREE_REFRESH_CERTS=0
# Last-resort fallback for other filters: "false" skips TLS checks for npm,
# build time only. Leave "true" everywhere else.
ARG NPM_STRICT_SSL=true

WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN netfree-certs \
    && npm config set strict-ssl "$NPM_STRICT_SSL" \
    && npm ci --no-audit --no-fund

COPY frontend/ ./
RUN node node_modules/typescript/bin/tsc --noEmit \
    && node node_modules/vite/bin/vite.js build

# `docker build --target test ...` (make dtest) runs the UI tests.
FROM build AS test
RUN node node_modules/vitest/vitest.mjs run

FROM nginx:1.27-alpine
COPY frontend/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /web/dist /usr/share/nginx/html
EXPOSE 80
