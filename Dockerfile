FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    ORCHESTRA_HOST=0.0.0.0 \
    ORCHESTRA_WORKSPACE=/workspace \
    ORCHESTRA_DATA_DIR=/data

WORKDIR /app

# The Claude Agent SDK wheel bundles the Claude Code CLI; no Node.js install is needed.
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
COPY examples ./examples
RUN pip install .

RUN useradd --create-home --uid 1000 orchestra \
    && mkdir -p /workspace /data \
    && chown -R orchestra:orchestra /workspace /data
USER orchestra

VOLUME ["/data", "/workspace"]
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
    CMD python -c "import urllib.request as u; u.urlopen('http://127.0.0.1:8000/health', timeout=3)"

CMD ["orchestra", "serve"]
