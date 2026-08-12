FROM node:24-trixie-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/
RUN apt-get update \
    && apt-get install -y --no-install-recommends python3 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY . .

RUN uv sync --locked --no-dev
RUN npm --prefix apps/blueprint/tui ci

CMD ["/app/.venv/bin/python", "-m", "apps.blueprint.main"]
