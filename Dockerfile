FROM ghcr.io/astral-sh/uv:0.13.7-python3.13-trixie-slim
WORKDIR /app
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1 UV_COMPILE_BYTECODE=1
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-install-project
COPY agent.py ./
CMD ["python", "agent.py", "start"]
