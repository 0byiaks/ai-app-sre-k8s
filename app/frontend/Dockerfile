# syntax=docker/dockerfile:1
# Unprivileged nginx: runs as UID 101 and listens on 8080, no root needed.
FROM nginxinc/nginx-unprivileged:1.27-alpine
ARG VERSION=dev
LABEL org.opencontainers.image.title="relay-frontend" \
      org.opencontainers.image.description="Relay web UI" \
      org.opencontainers.image.source="https://github.com/0byiaks/ai-app-sre-k8s" \
      org.opencontainers.image.version="${VERSION}"

# Where nginx forwards /api/ calls. Override it when the backend has a different address.
ENV BACKEND_URL=http://backend:8000

COPY nginx/default.conf.template /etc/nginx/templates/default.conf.template
COPY public/ /usr/share/nginx/html/

EXPOSE 8080
