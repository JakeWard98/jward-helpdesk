# syntax=docker/dockerfile:1
#
# One build definition, two runtime images. The build context is the repo root
# so both halves are reachable; pick a target explicitly:
#
#   docker build --target api -t jward-helpdesk-api .
#   docker build --target web -t jward-helpdesk-web .
#
# The targets do not share a runtime base on purpose. "api" carries Python and
# the application; "web" carries nginx and the built assets and nothing else -
# the internet-facing container has no database driver and no mail code in it.

# --- SPA build --------------------------------------------------------------
FROM node:22-alpine AS frontend-build
WORKDIR /app

COPY frontend/package.json frontend/package-lock.json* ./
# npm ci when a lockfile is committed, npm install on the first build.
RUN if [ -f package-lock.json ]; then npm ci; else npm install; fi

COPY frontend/tsconfig.json frontend/vite.config.ts frontend/index.html ./
COPY frontend/src ./src
RUN npm run build

# --- api + worker runtime ---------------------------------------------------
# Both containers run this image; compose picks the role with HELPDESK_ROLE and
# the worker overrides the command.
FROM python:3.12-slim AS api

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# curl/wget are deliberately absent from the runtime image; the healthcheck is
# a python module instead.
RUN adduser --system --group --uid 10001 helpdesk

COPY backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/app ./app

# /data/attachments is a volume in compose; create it so a bare `docker run`
# still works.
RUN mkdir -p /data/attachments && chown -R helpdesk:helpdesk /data /app

USER helpdesk

EXPOSE 8000

# 2 workers is plenty for a homelab helpdesk and keeps the DB pool small.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", \
     "--workers", "2", "--proxy-headers", "--forwarded-allow-ips", "*"]

# --- web runtime ------------------------------------------------------------
FROM nginxinc/nginx-unprivileged:1.27-alpine AS web

# nginx-unprivileged already runs as uid 101 and listens above 1024.
COPY frontend/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=frontend-build /app/dist /usr/share/nginx/html

EXPOSE 8080
