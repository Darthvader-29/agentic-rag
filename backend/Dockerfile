# backend/Dockerfile

# ── Stage 1: builder ──────────────────────────────────────────────────────────
# Compile/install all Python deps (and any wheels needing the build toolchain) into
# an isolated prefix. The toolchain (build-essential, *-dev headers) lives ONLY here
# so it never ships in the final image (smaller surface, faster pulls).
FROM python:3.12.6-slim AS builder

WORKDIR /app

# Build-time system deps: build-essential compiles C extensions, libpoppler-cpp-dev
# is needed by some PDF tooling. None of this is carried into the runtime stage.
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpoppler-cpp-dev \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies into a relocatable prefix (/install) we copy forward.
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# ── Stage 2: runtime ──────────────────────────────────────────────────────────
# Slim image with only the installed packages + app source, run as a non-root user.
FROM python:3.12.6-slim AS runtime

WORKDIR /app

# No runtime system packages are needed: PDF parsing uses pymupdf (fitz), which bundles
# its own MuPDF, and python-docx is pure Python — so the slim base suffices. The build
# toolchain (build-essential / libpoppler-cpp-dev) stayed in the builder stage and is
# intentionally dropped here.

# Carry the pre-built site-packages + console scripts (uvicorn) from the builder.
COPY --from=builder /install /usr/local

# Copy backend source code. A .dockerignore keeps .env*, .venv, caches, tests/docs
# and tmp_uploads/ out of the build context so no secret material lands in a layer.
COPY . .

# Default port FastAPI will listen on inside the container
ENV PORT=8000
EXPOSE 8000

# Run as a non-root user — minimizes container-escape blast radius when parsing
# untrusted PDF/docx. Create the user, give it the app dir, then drop privileges.
RUN useradd --create-home --uid 10001 appuser \
    && chown -R appuser:appuser /app
USER appuser

# Liveness probe: hit the app's own /health endpoint. urllib is in the stdlib, so this
# needs no extra package. A non-200 (or a connection error) marks the container unhealthy.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/health', timeout=4).status == 200 else 1)"

# Start FastAPI app
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
