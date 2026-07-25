# syntax=docker/dockerfile:1.7

# ============================================================================
# Builder stage: install all Python deps and the local package in editable mode.
# The builder has pip + git + build tools. Runtime stage won't need them.
# ============================================================================
FROM rmcrackan/libation:latest AS builder

ENV DEBIAN_FRONTEND=noninteractive

USER root

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        python3 \
        python3-pip \
        python3-venv \
        git \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies first (best layer caching).
COPY requirements.txt /app/requirements.txt
RUN pip install --break-system-packages --no-cache-dir -r /app/requirements.txt

# Copy ONLY the code that ships with the image. Never bake in user config,
# credentials, library UUIDs, or anything else that's mounted at runtime.
# Runtime config (libraries.yaml, .env) is mounted via docker-compose volumes.
COPY pyproject.toml /app/pyproject.toml
COPY src/ /app/src/
COPY abs_mcp/ /app/abs_mcp/

RUN pip install --break-system-packages --no-cache-dir -e .

# Strip test/docs/examples dirs from installed packages.
# KEEP .dist-info/ directories -- they're required for namespace package
# resolution (mcp, openai, etc. rely on them) and for pip metadata.
# Only remove RECORD/direct_url.json from inside .dist-info (saves a few MB).
RUN find /usr/local/lib/python3.12/dist-packages \
        \( -type d -name "tests" -o -type d -name "test" \
           -o -type d -name "examples" -o -type d -name "example" \
           -o -type d -name "docs" -o -type d -name "doc" \
           -o -type d -name "__pycache__" \
           -o -name "*.pyc" -o -name "*.pyo" \) \
        -prune -exec rm -rf {} + 2>/dev/null || true
RUN find /usr/local/lib/python3.12/dist-packages -name "RECORD" -delete 2>/dev/null || true

# Drop pip itself -- not needed at runtime, saves ~5MB.
RUN pip uninstall -y pip 2>/dev/null || true

# ============================================================================
# Runtime stage: start from the SAME Libation base (needs .NET + /libation/),
# install only the bare Python runtime (no pip, no git, no build tools), and
# copy the stripped site-packages from the builder. This avoids carrying build
# tools or duplicate package downloads.
# ============================================================================
FROM rmcrackan/libation:latest

ENV DEBIAN_FRONTEND=noninteractive

USER root

# Install only the Python runtime -- no pip, no git, no build tools.
# python3-minimal + libpython3.12 give us a working interpreter.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        python3-minimal \
        python3.12-minimal \
        libpython3.12 \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Copy the stripped site-packages from builder.
COPY --from=builder /usr/local/lib/python3.12/dist-packages /usr/local/lib/python3.12/dist-packages

# Copy the app code.
WORKDIR /app
COPY pyproject.toml /app/pyproject.toml
COPY src/ /app/src/
COPY abs_mcp/ /app/abs_mcp/

# Regenerate a fresh egg-info for the local package so Python can import it.
# (We deleted RECORD in the builder, so the original dist-info is incomplete.)
COPY docker/scripts/regen-egg-info.sh /tmp/regen-egg-info.sh
RUN bash /tmp/regen-egg-info.sh && rm /tmp/regen-egg-info.sh

# LIBATION_CLI points at the binary inside the Libation base image.
# MCP_ENV_FILE is intentionally NOT set here -- users must provide their
# own .env at runtime via docker-compose volume mount.
ENV LIBATION_CLI=/libation/LibationCli
ENV PYTHONUNBUFFERED=1

EXPOSE 8765

CMD ["python3", "-m", "abs_mcp.mcp_server"]