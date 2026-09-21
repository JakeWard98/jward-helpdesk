# syntax=docker/dockerfile:1

FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# curl/wget are deliberately absent from the runtime image; the healthcheck is
# a python module instead.
RUN adduser --system --group --uid 10001 helpdesk

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

# /data/attachments is a volume in compose; create it so a bare `docker run`
# still works.
RUN mkdir -p /data/attachments && chown -R helpdesk:helpdesk /data /app

USER helpdesk

EXPOSE 8000

# 2 workers is plenty for a homelab helpdesk and keeps the DB pool small.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", \
     "--workers", "2", "--proxy-headers", "--forwarded-allow-ips", "*"]
