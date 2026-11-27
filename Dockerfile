# VieForge Production Worker Container (Non-Root, 100% Clean-Room MIT)
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONUTF8=1 \
    PIP_NO_CACHE_DIR=1 \
    TIERFORGE_DATA_DIR=/data

WORKDIR /app

# Create unprivileged runtime user
RUN groupadd --system vieforge && useradd --system --gid vieforge --create-home vieforge \
    && mkdir -p /data && chown -R vieforge:vieforge /data /app

COPY pyproject.toml README.md AGENTS.md LICENSE ./
COPY core/ ./core/
COPY scripts/ ./scripts/
COPY config/ ./config/
COPY skills/ ./skills/
COPY examples/ ./examples/
COPY extensions/ ./extensions/

RUN pip install --upgrade pip && pip install ".[mcp]"

USER vieforge
VOLUME ["/data"]

HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD ["vieforge", "doctor", "--json"]

ENTRYPOINT ["vieforge"]
CMD ["mcp"]
