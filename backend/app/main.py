"""FastAPI application factory."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app import __version__, bootstrap
from app.api import admin, attachments, auth, health, templates, tickets
from app.config import settings
from app.middleware import (
    BodySizeLimitMiddleware,
    RequestContextMiddleware,
    SecurityHeadersMiddleware,
)

logging.basicConfig(
    level=getattr(logging, settings.log_level, logging.INFO),
    format="%(asctime)s %(levelname)-7s %(name)s %(message)s",
)
log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):  # noqa: ANN201
    bootstrap.run()
    log.info("helpdesk api %s ready (env=%s)", __version__, settings.app_env)
    yield


# The interactive docs are useful in development and are one more thing to
# attack in production, so they are only mounted outside prod.
app = FastAPI(
    title="jward-helpdesk",
    version=__version__,
    lifespan=lifespan,
    docs_url=None if settings.is_prod else "/api/docs",
    redoc_url=None,
    openapi_url=None if settings.is_prod else "/api/openapi.json",
)

app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(RequestContextMiddleware)
app.add_middleware(
    BodySizeLimitMiddleware,
    max_bytes=settings.max_attachment_bytes + 2 * 1024 * 1024,
)

# Same-origin in the normal deployment (nginx proxies /api), so CORS stays off
# unless origins were configured explicitly.
if settings.cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.allowed_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type", "X-CSRF-Token"],
        max_age=600,
    )


@app.exception_handler(RequestValidationError)
async def validation_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    """Return a readable message without echoing the submitted values back."""
    fields = []
    for error in exc.errors():
        location = ".".join(str(part) for part in error.get("loc", ()) if part != "body")
        fields.append(f"{location or 'request'}: {error.get('msg', 'invalid')}")
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={"detail": "; ".join(fields[:6]) or "Invalid request"},
    )


@app.exception_handler(Exception)
async def unhandled_handler(request: Request, exc: Exception) -> JSONResponse:
    """Log the detail, tell the client nothing.

    A stack trace or driver message in a response body is free reconnaissance.
    """
    request_id = getattr(request.state, "request_id", "-")
    log.exception("unhandled error rid=%s path=%s", request_id, request.url.path)
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"detail": "Something went wrong", "request_id": request_id},
    )


app.include_router(health.router, prefix="/api")
app.include_router(auth.router, prefix="/api")
app.include_router(tickets.router, prefix="/api")
app.include_router(templates.router, prefix="/api")
app.include_router(attachments.router, prefix="/api")
app.include_router(admin.router, prefix="/api")
