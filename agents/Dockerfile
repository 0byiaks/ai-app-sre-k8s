FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Run as a normal user, not root.
RUN groupadd --system --gid 10001 app && useradd --system --uid 10001 --gid app --no-create-home app

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

# The agent code, including runbook.md (the system overview).
COPY sre_agent ./sre_agent

USER 10001
EXPOSE 8000
CMD ["uvicorn", "sre_agent.server:app", "--host", "0.0.0.0", "--port", "8000"]