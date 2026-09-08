# Multi-stage Dockerfile for Business Case Generator
# Stage 1: Build React frontend (on native platform to avoid esbuild QEMU crashes)
# Stage 2: Build Python backend with frontend (target platform)

# ============================================================================
# Stage 1: Build Frontend (native platform - avoids esbuild Go binary QEMU crash)
# ============================================================================
FROM --platform=$BUILDPLATFORM public.ecr.aws/docker/library/node:20-alpine3.21 AS frontend-build

WORKDIR /app/ui

# Copy package files
COPY ui/package*.json ./

# Install dependencies
RUN npm ci

# Copy frontend source
COPY ui/ ./

# Build frontend for production
RUN npm run build

# ============================================================================
# Stage 2: Build Backend with Frontend
# ============================================================================

FROM public.ecr.aws/docker/library/python:3.11-slim-bookworm AS base

# Force fresh package index and upgrade all packages to latest patched versions
# Addresses linux kernel CVEs (6.1.164 -> 6.1.170+): CVE-2026-23428, CVE-2026-23450,
# CVE-2026-23455, CVE-2026-31402, CVE-2026-31405, CVE-2026-31414, and 150+ others
# Previously: openssl CVEs (3.0.18 -> 3.0.19): CVE-2026-31790, CVE-2026-28387,
# CVE-2026-28389, CVE-2026-31789, CVE-2026-28390, CVE-2026-28388
RUN echo "Package cache bust: 2026-05-05" > /dev/null

WORKDIR /app

# Install system dependencies and upgrade all packages to patch CVEs
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    g++ \
    && apt-get upgrade -y \
    && apt-get dist-upgrade -y \
    && apt-get install -y --only-upgrade linux-libc-dev \
    && rm -rf /var/lib/apt/lists/*

# Copy Python requirements
COPY requirements.txt ./

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY agents/ ./agents/
COPY prompt_library/ ./prompt_library/
COPY utils/ ./utils/
COPY ui/backend/ ./ui/backend/

# Copy frontend build from stage 1
COPY --from=frontend-build /app/ui/build ./ui/dist

# Create necessary directories
RUN mkdir -p /tmp/input /tmp/output

# Create non-root user for security
RUN groupadd -r appuser && useradd -r -g appuser -d /app -s /sbin/nologin appuser \
    && chown -R appuser:appuser /app /tmp/input /tmp/output

# ---------------------------------------------------------------------------
# AWS Lambda Web Adapter — lets this exact container run as a Lambda function
# with no application code changes. Outside Lambda the file is inert: only the
# Lambda runtime reads /opt/extensions. One image therefore serves both the
# Lambda web tier and the Fargate job tier (ECS RunTask for long generations).
# ---------------------------------------------------------------------------
COPY --from=public.ecr.aws/awsguru/aws-lambda-adapter:0.9.1 /lambda-adapter /opt/extensions/lambda-adapter

# Set environment variables
ENV FLASK_ENV=production
ENV PYTHONUNBUFFERED=1

# Lambda Web Adapter config. Ignored when the container runs outside Lambda.
# readiness path avoids the adapter probing "/" (the React SPA) during cold start.
#
# Invoke mode stays at the adapter's default (buffered): the app is fronted by
# an HTTP API, which cannot parse a streamed response. response_stream only
# works behind a Function URL, and those are blocked on this account.
ENV AWS_LWA_PORT=8080
ENV AWS_LWA_READINESS_CHECK_PATH=/api/health

# Switch to non-root user
USER appuser

# Expose port
EXPOSE 8080

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD python -c "import requests; requests.get('http://localhost:8080/api/health', timeout=5)"

# Run with Gunicorn
CMD ["gunicorn", \
     "--bind", "0.0.0.0:8080", \
     "--workers", "2", \
     "--threads", "4", \
     "--timeout", "900", \
     "--access-logfile", "-", \
     "--error-logfile", "-", \
     "--chdir", "/app/ui/backend", \
     "app:app"]
