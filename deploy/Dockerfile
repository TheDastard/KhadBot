# KhadBot hosted image: one image, three roles. The command picks the role (see deploy/README.md):
#
#   api      serves the dashboard API on 0.0.0.0:8000 from Postgres and the bucket (the default)
#   migrate  applies the database migrations and exits
#   worker   runs the job queue worker (ingest, process and review jobs from Postgres)
#
# Build from the repository root:  docker build -f deploy/Dockerfile -t khadbot .
#
# The image carries the locked `api` and `hosted` dependency groups only (no CLI, no LLM providers, no dev tools),
# the source, and config/ (every cache key hashes the authored files, so the image and the data must agree on them).
# Nothing from the host leaks in: .dockerignore excludes everything the COPY lines below do not name.

ARG PYTHON_VERSION=3.14
ARG UV_VERSION=0.13.5

FROM ghcr.io/astral-sh/uv:${UV_VERSION} AS uv

# --- Build stage: resolve and install from uv.lock ---------------------------------------------------------------
FROM python:${PYTHON_VERSION}-slim AS build
COPY --from=uv /uv /usr/local/bin/uv

# The venv lives at its final path, so copying it to the runtime stage keeps every script shebang valid. The system
# interpreter is used as it is: the base image's Python is the version .python-version names.
ENV UV_PROJECT_ENVIRONMENT=/app/.venv \
    UV_PYTHON_DOWNLOADS=never \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy
WORKDIR /app

# Dependencies first, so a source-only change reuses this layer.
COPY pyproject.toml uv.lock .python-version ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-install-project --extra api --extra hosted

# The project itself, installed editable: the code finds config/ beside src/, so both are copied to the same place.
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --extra api --extra hosted

# --- Runtime stage -----------------------------------------------------------------------------------------------
FROM python:${PYTHON_VERSION}-slim AS runtime

RUN groupadd --system --gid 10001 khadbot \
    && useradd --system --uid 10001 --gid khadbot --home-dir /home/khadbot --create-home khadbot \
    && mkdir -p /var/lib/khadbot \
    && chown khadbot:khadbot /var/lib/khadbot

WORKDIR /app
COPY --from=build --chown=khadbot:khadbot /app/.venv ./.venv
COPY --chown=khadbot:khadbot src ./src
COPY --chown=khadbot:khadbot config ./config
COPY --chown=khadbot:khadbot main.py ./main.py
COPY --chown=khadbot:khadbot scripts/data/seed_stack.py ./scripts/data/seed_stack.py
COPY --chown=khadbot:khadbot scripts/dev/queue_smoke.py ./scripts/dev/queue_smoke.py

# KHADBOT_DATA_DIR is scratch space only: hosted mode keeps its data in Postgres and the bucket, and nothing from the
# host is mounted over it. Everything else (database URL, bucket, credentials) comes from the environment at run time.
ENV PATH="/app/.venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    KHADBOT_DATA_DIR=/var/lib/khadbot

USER khadbot
EXPOSE 8000
ENTRYPOINT ["khadbot"]
CMD ["api"]
