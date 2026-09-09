# Python quant engine + MCP server — one container, two logically separate
# packages (architecture.md §19).
# Build context: repository root.

FROM python:3.13-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

# build-essential is needed for any source-only wheel in the scientific stack;
# it lives in the builder stage only so it never ships in the runtime image.
FROM base AS build
RUN apt-get update && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app

# Dependency metadata first, so the (slow) scientific-stack install is cached
# independently of source edits.
COPY pyproject.toml README.md ./
RUN mkdir -p services && touch services/__init__.py \
    && python -m venv /opt/venv \
    && /opt/venv/bin/pip install --upgrade pip \
    && /opt/venv/bin/pip install .

FROM base AS runtime
WORKDIR /app
COPY --from=build /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY services/ ./services/
COPY pyproject.toml README.md ./
RUN pip install --no-deps -e .

# Run unprivileged: nothing in this service needs root.
RUN useradd --create-home --uid 10001 quant && chown -R quant:quant /app
USER quant

EXPOSE 8000
CMD ["python", "-m", "services.quant.webapi"]
