FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

# Dependencies first so source edits do not invalidate the layer.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY src ./src
COPY prompts ./prompts
COPY family.yaml aegra.json README.md ./
RUN uv sync --frozen --no-dev

ENV PATH="/app/.venv/bin:$PATH" \
    PORT=2026

EXPOSE 2026

# `aegra serve` runs the API and its background workers in ONE process
# (WORKER_COUNT x N_JOBS_PER_WORKER). There is no separate worker command.
CMD ["aegra", "serve"]
