"""Uploads and downloads.

Downloads are always served from our own origin with ``nosniff`` and, for
anything that is not a known-safe image, ``Content-Disposition: attachment``.
That stops an uploaded ``.html`` or ``.svg`` from executing in the context of
the helpdesk's origin and stealing a session.
"""

from __future__ import annotations

import logging
from urllib.parse import quote

from fastapi import APIRouter, File, HTTPException, Request, UploadFile, status
from fastapi.responses import Response

from app.config import settings
from app.deps import CurrentUser, DbSession
from app.models import Attachment, Ticket
from app.schemas import UploadOut
from app.security import sessions
from app.services import audit, storage
from app.services import tickets as ticket_service

log = logging.getLogger(__name__)
router = APIRouter(tags=["attachments"])

# Served inline only for these. Note SVG is deliberately absent: it can carry
# script and would run on our origin.
INLINE_DISPOSITION_TYPES = storage.INLINE_SAFE_TYPES


def _load_attachment(db: DbSession, attachment_id: str, viewer) -> Attachment:  # noqa: ANN001
    attachment = db.get(Attachment, attachment_id)
    if attachment is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    if attachment.ticket_id is None:
        # Still an unclaimed upload: only the uploader can see it.
        if attachment.uploaded_by_id != viewer.id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
        return attachment

    ticket = db.get(Ticket, attachment.ticket_id)
    if ticket is None or not ticket_service.can_view(viewer, ticket):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    # An internal note's attachment must not leak to the requester.
    if attachment.message and attachment.message.is_internal and not viewer.is_staff:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    return attachment


@router.post("/uploads", response_model=UploadOut, status_code=status.HTTP_201_CREATED)
async def upload(
    request: Request,
    db: DbSession,
    context: CurrentUser,
    file: UploadFile = File(...),
) -> UploadOut:
    """Stage a file.

    The returned id is passed to ticket creation or to a reply, which claims
    the file. Unclaimed uploads are swept up by the worker after 24 hours.
    """
    # Read with a hard ceiling rather than trusting Content-Length.
    data = await file.read(settings.max_attachment_bytes + 1)
    await file.close()
    if not data:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Empty file")
    if len(data) > settings.max_attachment_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"Files must be {settings.max_attachment_mb} MB or smaller",
        )

    try:
        attachment = ticket_service.attach_file(
            db,
            None,
            None,
            filename=file.filename or "attachment",
            content_type=file.content_type or "",
            data=data,
            uploaded_by=context.user,
        )
    except storage.AttachmentTooLarge as exc:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail=str(exc)
        ) from exc

    audit.record(
        db,
        action="attachment.uploaded",
        actor=context.user,
        object_type="attachment",
        object_id=attachment.id,
        ip_address=sessions.client_ip(request),
        detail={"filename": attachment.filename, "bytes": attachment.size_bytes},
    )
    db.commit()
    return UploadOut(
        id=attachment.id,
        filename=attachment.filename,
        content_type=attachment.content_type,
        size_bytes=attachment.size_bytes,
    )


def _file_response(attachment: Attachment, *, inline: bool) -> Response:
    try:
        data = storage.read(attachment.storage_path)
    except (OSError, ValueError) as exc:
        log.error("attachment %s missing from disk: %s", attachment.id, exc)
        raise HTTPException(
            status_code=status.HTTP_410_GONE, detail="This file is no longer available"
        ) from exc

    content_type = attachment.content_type or "application/octet-stream"
    serve_inline = inline and content_type in INLINE_DISPOSITION_TYPES
    if not serve_inline:
        content_type = "application/octet-stream"

    disposition = "inline" if serve_inline else "attachment"
    # RFC 5987 encoding keeps non-ASCII names intact without header injection.
    filename = quote(attachment.filename)
    return Response(
        content=data,
        media_type=content_type,
        headers={
            "Content-Disposition": f"{disposition}; filename*=UTF-8''{filename}",
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "default-src 'none'; img-src 'self'; sandbox",
            "Cache-Control": "private, max-age=600",
        },
    )


@router.get("/attachments/{attachment_id}")
def download(attachment_id: str, db: DbSession, context: CurrentUser) -> Response:
    attachment = _load_attachment(db, attachment_id, context.user)
    return _file_response(attachment, inline=False)


@router.get("/attachments/{attachment_id}/inline")
def inline(attachment_id: str, db: DbSession, context: CurrentUser) -> Response:
    """Serve an image referenced from a mail body by ``cid:``."""
    attachment = _load_attachment(db, attachment_id, context.user)
    return _file_response(attachment, inline=True)
