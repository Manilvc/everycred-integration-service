# Multi-stage build for the EveryCRED integration service.
#
# Dependencies are installed into a virtualenv in the builder stage and
# only that virtualenv reaches the runtime image: no compiler, no uv, and no
# build cache in production. Mirrors the audit service's image layout.

# ---------------------------------------------------------------------------
# Builder
# ---------------------------------------------------------------------------
FROM python:3.13-slim-bookworm AS builder

# uv is copied from its own image at a pinned version (the same pin the
# audit service uses) instead of being installed from the package index.
COPY --from=ghcr.io/astral-sh/uv:0.9.7 /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /build

# Manifests first so the slow dependency layer is rebuilt only when they
# change. --frozen fails the build if uv.lock and pyproject.toml disagree,
# so the image always has exactly the versions that were tested.
COPY pyproject.toml uv.lock README.md ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project

# ---------------------------------------------------------------------------
# Runtime
# ---------------------------------------------------------------------------
FROM python:3.13-slim-bookworm AS runtime

RUN groupadd --gid 10001 integration \
 && useradd --uid 10001 --gid integration --no-create-home \
      --shell /usr/sbin/nologin integration

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONFAULTHANDLER=1 \
    PORT=8030

WORKDIR /app

COPY --from=builder --chown=integration:integration /build/.venv /app/.venv
COPY --chown=integration:integration app ./app
COPY --chown=integration:integration migrations ./migrations
COPY --chown=integration:integration alembic.ini pyproject.toml README.md ./
COPY --chown=integration:integration --chmod=0755 deploy/docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh

# Non-root. The process writes nothing to disk: logs go to stdout, state
# lives in MySQL and the secret store.
USER 10001:10001

EXPOSE 8030

# The slim image has no curl; Python's standard library is enough.
HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=5 \
    CMD ["python", "-c", "import os, sys, urllib.request; sys.exit(0 if urllib.request.urlopen(f\"http://127.0.0.1:{os.environ.get('PORT', '8030')}/health/live\", timeout=3).status == 200 else 1)"]

# `serve` runs the API, `migrate` applies Alembic migrations and exits.
ENTRYPOINT ["docker-entrypoint.sh"]
CMD ["serve"]
