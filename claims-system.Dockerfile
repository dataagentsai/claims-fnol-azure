# syntax=docker/dockerfile:1.7
# The claims system (claims_system): our MCP server on PostgreSQL, the eight
# claims-system operations of the AOAS. Internal ingress only in Azure.
#
# Build from this repository's root (FINDINGS F-44):
#
#   docker buildx build -f claims-system.Dockerfile \
#     --build-context harness=../reference-agent/packages/agent-harness \
#     --build-context worlds=../clean-ai-engineering/gates/motor-claims-fnol/worlds \
#     -t ghcr.io/<owner>/claims-fnol-claims-system:<tag> .
#
#   harness  the agent_harness library (a path dependency in pyproject.toml)
#   worlds   the FNOL world the seed reads (claims_system.__main__.WORLD,
#            ../clean-ai-engineering/gates/motor-claims-fnol/worlds/...)
#
# On start: migrate (idempotent DDL), seed only a never-seeded database
# (`--if-empty`), serve on 9050. With scale-to-zero every cold start is a
# start, so seeding every time would reset the demo claims (FINDINGS F-45);
# reset on purpose with `python -m claims_system seed --fresh`.
#
# Reads CLAIMS_DATABASE_URL and CLAIMS_RECORDS_DATABASE_URL from the
# environment (Container Apps fills both from Key Vault): its own login, the
# claims_system role (A4), never the agent's.

ARG PYTHON_IMAGE=python:3.13-slim

FROM ${PYTHON_IMAGE} AS base
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

FROM base AS build
COPY --from=ghcr.io/astral-sh/uv:0.11.6 /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app/venv
COPY --from=harness . /app/reference-agent/packages/agent-harness
WORKDIR /app/claims-fnol-azure
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project --no-editable

FROM base
RUN useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin app
COPY --from=build /app/venv /app/venv
COPY --from=worlds . /app/clean-ai-engineering/gates/motor-claims-fnol/worlds
WORKDIR /app/claims-fnol-azure
COPY src ./src
ENV PATH=/app/venv/bin:$PATH \
    PYTHONPATH=/app/claims-fnol-azure/src
USER 10001
EXPOSE 9050
CMD ["sh", "-c", "python -m claims_system migrate && python -m claims_system seed --if-empty && exec python -m claims_system serve --host 0.0.0.0 --port 9050"]
