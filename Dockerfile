# syntax=docker/dockerfile:1.7
FROM python:3.12-slim-bookworm AS base
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 UV_LINK_MODE=copy UV_COMPILE_BYTECODE=1 \
    TZ=Asia/Seoul PATH="/app/.venv/bin:/usr/local/bin:$PATH"
RUN apt-get update && apt-get install -y --no-install-recommends \
      ca-certificates curl git tzdata build-essential \
    && rm -rf /var/lib/apt/lists/*

# supercronic: cron for containers (env passthrough, stdout logging)
ARG SUPERCRONIC_VERSION=v0.2.33
RUN ARCH=$(dpkg --print-architecture) && \
    curl -fsSL -o /usr/local/bin/supercronic \
      "https://github.com/aptible/supercronic/releases/download/${SUPERCRONIC_VERSION}/supercronic-linux-${ARCH}" && \
    chmod +x /usr/local/bin/supercronic

# Node 22 + bird CLI (X/Twitter via cookie auth). Pinned fork commit for reproducibility.
ARG NODE_MAJOR=22
ARG BIRD_REPO=https://github.com/geekavan/bird.git
ARG BIRD_REF=nix-build
RUN curl -fsSL https://deb.nodesource.com/setup_${NODE_MAJOR}.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && corepack enable \
    && rm -rf /var/lib/apt/lists/*
# The fork's lockfile drifts from its patchedDependencies config, so install without --frozen-lockfile.
# pnpm 10+ exits non-zero on ignored build scripts (esbuild) unless approved → config.dangerouslyAllowAllBuilds.
RUN git clone --depth 1 --branch "${BIRD_REF}" "${BIRD_REPO}" /opt/bird \
    && cd /opt/bird && corepack pnpm config set dangerouslyAllowAllBuilds true \
    && corepack pnpm install --no-frozen-lockfile && corepack pnpm run build:dist \
    && corepack pnpm prune --prod \
    && printf '#!/bin/sh\nexec node /opt/bird/dist/cli.js "$@"\n' > /usr/local/bin/bird \
    && chmod +x /usr/local/bin/bird \
    && bird --version

COPY --from=ghcr.io/astral-sh/uv:0.5 /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev --no-install-project
COPY . .
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev
ENTRYPOINT ["tokres"]
CMD ["--help"]
