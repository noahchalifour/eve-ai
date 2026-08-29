FROM python:3.12-slim

# Pinned: unpinned build tooling in a cluster-bound image means the image
# is not reproducible and a bad uv release enters it silently.
COPY --from=ghcr.io/astral-sh/uv:0.10.0 /uv /usr/local/bin/uv

WORKDIR /app

# Dependencies first so source edits do not invalidate the layer.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY src ./src
COPY prompts ./prompts
COPY family.yaml aegra.json README.md ./
COPY alembic.ini ./
COPY alembic ./alembic
RUN uv sync --frozen --no-dev

ENV PATH="/app/.venv/bin:$PATH" \
    PORT=2026

EXPOSE 2026

# Nothing here needs root, and the cluster manifests declare `runAsNonRoot`.
RUN useradd --system --uid 10001 --no-create-home eve \
    && chown -R eve:eve /app
USER 10001

# `aegra serve` runs the API and its background workers in ONE process
# (WORKER_COUNT x N_JOBS_PER_WORKER). There is no separate worker command.
#
# `eve-migrate` applies Eve's own memory schema first; Aegra runs its own
# Alembic migrations separately at startup. `exec` so aegra, not sh, receives
# SIGTERM - without it the pod takes the full termination grace period to die.
CMD ["sh", "-c", "eve-migrate && exec aegra serve"]
