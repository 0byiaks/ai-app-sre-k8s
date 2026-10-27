# syntax=docker/dockerfile:1
# ---- build stage: install dependencies into a virtualenv ----
FROM python:3.12-slim AS build
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
COPY requirements.txt .
RUN pip install -r requirements.txt

# ---- runtime stage: small image, non-root user, no build tools ----
FROM python:3.12-slim AS runtime
ARG VERSION=dev
LABEL org.opencontainers.image.title="relay-backend" \
      org.opencontainers.image.description="Relay API: AI chat app" \
      org.opencontainers.image.source="https://github.com/0byiaks/ai-app-sre-k8s" \
      org.opencontainers.image.version="${VERSION}"

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN groupadd --system --gid 10001 app && useradd --system --uid 10001 --gid app --no-create-home app
COPY --from=build /opt/venv /opt/venv
WORKDIR /app
COPY relay ./relay

USER 10001
EXPOSE 8000
# No shell form: uvicorn is PID 1 and receives SIGTERM directly for graceful shutdown.
CMD ["uvicorn", "relay.main:app", "--host", "0.0.0.0", "--port", "8000", "--timeout-graceful-shutdown", "20", "--no-server-header"]
