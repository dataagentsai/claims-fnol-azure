# syntax=docker/dockerfile:1.7
# The claims agent (claims_fnol_app): the chat page, /chat, the handler's desk.
#
# Build from this repository's root, naming the two sibling checkouts the app
# reads (FINDINGS F-44):
#
#   docker buildx build -f agent.Dockerfile \
#     --build-context harness=../reference-agent/packages/agent-harness \
#     --build-context stacks=../clean-ai-engineering/stacks \
#     -t ghcr.io/<owner>/claims-fnol-agent:<tag> .
#
#   harness  the agent_harness library (a path dependency in pyproject.toml)
#   stacks   the stack profiles harness-profile.yaml extends
#            (../clean-ai-engineering/stacks/azure.yaml -> open-stack.yaml)
#
# The image lays them out as the repositories sit on the Mac, so every relative
# path in the code and the YAML resolves unchanged:
#   /app/claims-fnol-azure                       this repository (source, config)
#   /app/reference-agent/packages/agent-harness  build stage only
#   /app/clean-ai-engineering/stacks             the stack profiles
#
# Runs as a non-root user on port 8000 with CLAIMS_FNOL_ENV=azure: the overlay
# config/azure.yaml, whose settings come from the environment Container Apps
# sets (infra/main.bicep) and from Key Vault.

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
# Dependencies only, from the lock, no dev extra (so no agenttwin), the harness
# installed as a copy. The project itself runs from src/ (PYTHONPATH below):
# its settings find config/ beside src/, which an installed copy would not.
RUN uv sync --frozen --no-install-project --no-editable

FROM base
RUN useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin app
COPY --from=build /app/venv /app/venv
COPY --from=stacks . /app/clean-ai-engineering/stacks
WORKDIR /app/claims-fnol-azure
COPY harness-profile.yaml ./
COPY config ./config
COPY src ./src
ENV PATH=/app/venv/bin:$PATH \
    PYTHONPATH=/app/claims-fnol-azure/src \
    CLAIMS_FNOL_ENV=azure
USER 10001
EXPOSE 8000
CMD ["python", "-m", "claims_fnol_app", "--host", "0.0.0.0", "--port", "8000"]
