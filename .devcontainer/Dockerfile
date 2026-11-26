# syntax=docker/dockerfile:1
#
# Development image for facelessCreation.
# - Python 3.11 (Debian bookworm), Node.js 22, FFmpeg, eSpeak NG, SQLite
# - No NVIDIA driver and no CUDA toolkit inside the image: the host driver
#   (and the NVENC library) is injected at runtime by the NVIDIA Container
#   Toolkit. CUDA libraries for optional GPU inference come from pip wheels.

FROM node:22-bookworm-slim AS node

FROM python:3.11-slim-bookworm

# hadolint ignore=DL3064
ARG USERNAME=vscode
ARG USER_UID=1000
ARG USER_GID=1000

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# System packages. Debian's ffmpeg is built with NVENC support (loaded from
# the host driver at runtime) and libx264 for the CPU fallback.
# hadolint ignore=DL3008
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      ffmpeg espeak-ng libsndfile1 sqlite3 \
      git openssh-client curl ca-certificates jq less procps sudo \
      build-essential pkg-config \
 && rm -rf /var/lib/apt/lists/*

# Node.js 22 + npm from the official Node image
COPY --from=node /usr/local/bin/node /usr/local/bin/node
COPY --from=node /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -s ../lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
 && ln -s ../lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx \
 && node --version && npm --version

# Non-root user (sudo is kept for development convenience)
RUN groupadd --gid "${USER_GID}" "${USERNAME}" \
 && useradd --uid "${USER_UID}" --gid "${USER_GID}" -m -s /bin/bash "${USERNAME}" \
 && echo "${USERNAME} ALL=(root) NOPASSWD:ALL" > "/etc/sudoers.d/${USERNAME}" \
 && chmod 0440 "/etc/sudoers.d/${USERNAME}"

# Mount points for named volumes. Creating them here, owned by the user,
# makes Docker give new volumes the same ownership.
RUN mkdir -p \
      "/home/${USERNAME}/.claude" \
      "/home/${USERNAME}/.cache/huggingface" \
      "/home/${USERNAME}/.cache/torch" \
      "/home/${USERNAME}/.venvs" \
      "/home/${USERNAME}/.npm-global" \
 && chown -R "${USERNAME}:${USERNAME}" "/home/${USERNAME}"

USER ${USERNAME}

ENV NPM_CONFIG_PREFIX=/home/${USERNAME}/.npm-global \
    PATH=/home/${USERNAME}/.venvs/app/bin:/home/${USERNAME}/.npm-global/bin:${PATH}

# Claude Code and the Angular CLI, installed as the user so they can self-update
# hadolint ignore=DL3016
RUN npm install -g @anthropic-ai/claude-code @angular/cli \
 && npm cache clean --force

WORKDIR /workspaces/facelesscreation
