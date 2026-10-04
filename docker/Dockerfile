# The agent image for `caliper run --container` and `caliper vet --container`.
#
#   docker build -t caliper-agent docker/
#
# Caliper mounts each attempt's isolated home and workdir at the same paths,
# runs the agent as your user with every capability dropped, and gives it a
# network whose only way out is caliper's egress proxy (docs/adr/0035). The
# image only has to supply the agent CLIs and the tools a skill commonly runs.
# Credentials are never baked in: they reach the container through the isolated
# home, the way they reach an uncontained attempt.
#
# Pin a CLI version with --build-arg, e.g.
#   docker build -t caliper-agent --build-arg CODEX_VERSION=0.50.0 docker/
FROM node:22-bookworm-slim

ARG CLAUDE_CODE_VERSION=latest
ARG CODEX_VERSION=latest

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates curl git jq python3 python3-venv ripgrep \
    && rm -rf /var/lib/apt/lists/*

RUN npm install -g \
        "@anthropic-ai/claude-code@${CLAUDE_CODE_VERSION}" \
        "@openai/codex@${CODEX_VERSION}" \
    && npm cache clean --force

# Caliper runs the agent as the invoking user's uid, which has no entry in
# /etc/passwd here; HOME and the working directory come from the run.
ENV DISABLE_AUTOUPDATER=1
