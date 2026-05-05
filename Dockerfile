# Use the requested Python runtime on Debian Bookworm slim for a smaller image.
FROM python:3.14-slim-bookworm

# Keep container behavior predictable:
# - no .pyc files written to disk
# - stdout/stderr unbuffered for real-time logs
# - quieter/faster pip behavior for CI and container builds
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

# Run the application from a dedicated working directory.
WORKDIR /app

# Install only the minimal OS package required for TLS trust chains.
RUN apt-get update \
    && apt-get install --no-install-recommends -y ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Copy ONLY the files the runtime needs.
#
# `.dockerignore` already default-denies the build context, but we keep
# the COPY surface explicit here as defense-in-depth: any future change
# that loosens the ignore file still has to pass through this list. In
# particular, NEVER replace these with `COPY . /app` — that's how
# `.env`, `.git`, and `.venv` ended up in the image historically.
COPY pyproject.toml uv.lock README.md /app/
COPY main.py /app/main.py
COPY src/ /app/src/
COPY schemas/ /app/schemas/
# Bundle the repo-authored skill so FastMCP's SkillsDirectoryProvider
# (registered in src/server/mcp_server.py) can discover it at runtime.
# `_resolve_skills_root()` looks for `<repo-root>/skills/`, which inside
# the container is `/app/skills/` once this COPY lands. Without this
# line the provider boots gracefully but exposes no skill resources.
COPY skills/ /app/skills/

# Install project dependencies and the local package.
RUN python -m pip install --upgrade pip \
    && python -m pip install .

# Drop root privileges for runtime security.
RUN useradd --create-home --uid 10001 appuser \
    && chown -R appuser:appuser /app
USER appuser

# Document the default HTTP port used by main.py.
EXPOSE 8000

# Start the FastMCP HTTP server.
CMD ["python", "main.py"]
