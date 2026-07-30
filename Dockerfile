FROM ghcr.io/astral-sh/uv:python3.12-trixie-slim

WORKDIR /app
COPY . .

RUN uv sync --locked --no-dev

CMD ["/app/.venv/bin/python", "main.py"]
