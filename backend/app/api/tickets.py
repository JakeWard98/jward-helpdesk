"""Ticket listing, creation, replies and updates."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query, Request, status
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.deps import CurrentUser, DbSession
from app.models import Attachment, Template, Ticket, TicketMessage, User
from app.schemas import (
    TemplateFieldOut,
    TicketCreateRequest,
    TicketDetailOut,
    TicketListResponse,
    TicketMessageOut,
    TicketReplyRequest,
    TicketSummaryOut,
    TicketUpdateRequest,
)
from app.security import sessions
from app.services import audit, email_outbound
from app.services import templates as template_service
from app.services import tickets as ticket_service

log = logging.getLogger(__name__)
router = APIRouter(prefix="/tickets", tags=["tickets"])

MAX_PAGE_SIZE = 100


def _summary(
    ticket: Ticket, *, message_count: int = 0, attachment_count: int = 0
) -> TicketSummaryOut:
    return TicketSummaryOut(
        id=ticket.id,
        key=ticket.key,
        number=ticket.number,
        subject=ticket.subject,
        status=ticket.status,
        priority=ticket.priority,
        source=ticket.source,
        requester_name=ticket.requester.display_name or ticket.requester.email,
        requester_email=ticket.requester.email,
        assignee_name=(ticket.assignee.display_name or ticket.assignee.email)
        if ticket.assignee
        else None,
        template_name=ticket.template.name if ticket.template else None,
        created_at=ticket.created_at,
        last_activity_at=ticket.last_activity_at,
        message_count=message_count,
        attachment_count=attachment_count,
    )


def _visible_messages(ticket: Ticket, viewer: User) -> list[TicketMessage]:
    """Requesters never see internal notes."""
    if viewer.is_staff:
        return list(ticket.messages)
    return [m for m in ticket.messages if not m.is_internal]


def _detail(ticket: Ticket, viewer: User) -> TicketDetailOut:
    messages = _visible_messages(ticket, viewer)
    summary = _summary(
        ticket,
        message_count=len(messages),
        attachment_count=sum(len(m.attachments) for m in messages),
    )
    template_fields = [
        TemplateFieldOut(**field) for field in (ticket.template.fields if ticket.template else [])
    ]
    return TicketDetailOut(
        **summary.model_dump(),
        template_id=ticket.template_id,
        assignee_id=ticket.assignee_id,
        field_values=ticket.field_values or {},
        template_fields=template_fields,
        messages=[TicketMessageOut.model_validate(m) for m in messages],
    )


def _load_ticket(db: Session, ticket_id: str, viewer: User) -> Ticket:
    ticket = (
        db.query(Ticket)
        .options(
            selectinload(Ticket.messages).selectinload(TicketMessage.attachments),
            selectinload(Ticket.requester),
            selectinload(Ticket.assignee),
            selectinload(Ticket.template),
        )
        .filter(Ticket.id == ticket_id)
        .one_or_none()
    )
    # Same 404 whether the ticket is missing or simply not yours, so the
    # endpoint cannot be used to probe which ticket ids exist.
    if ticket is None or not ticket_service.can_view(viewer, ticket):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ticket not found")
    return ticket


@router.get("", response_model=TicketListResponse)
def list_tickets(
    db: DbSession,
    context: CurrentUser,
    status_filter: Annotated[list[str] | None, Query(alias="status")] = None,
    assignee: Annotated[str | None, Query()] = None,
    template_id: Annotated[str | None, Query()] = None,
    q: Annotated[str | None, Query(max_length=200)] = None,
    view: Annotated[Literal["all", "open", "mine", "unassigned"], Query()] = "all",
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = 25,
) -> TicketListResponse:
    viewer = context.user
    query = db.query(Ticket).options(
        selectinload(Ticket.requester),
        selectinload(Ticket.assignee),
        selectinload(Ticket.template),
    )

    if not viewer.is_staff:
        query = query.filter(Ticket.requester_id == viewer.id)

    if view == "open":
        query = query.filter(Ticket.status.in_(ticket_service.OPEN_STATUSES))
    elif view == "mine" and viewer.is_staff:
        query = query.filter(Ticket.assignee_id == viewer.id)
    elif view == "unassigned" and viewer.is_staff:
        query = query.filter(Ticket.assignee_id.is_(None))

    if status_filter:
        query = query.filter(Ticket.status.in_(status_filter[:6]))
    if assignee and viewer.is_staff:
        query = query.filter(Ticket.assignee_id == assignee)
    if template_id:
        query = query.filter(Ticket.template_id == template_id)
    if q:
        # Parameterised LIKE - never string-formatted into SQL.
        pattern = f"%{q.strip()}%"
        conditions = [Ticket.subject.ilike(pattern)]
        if q.strip().upper().startswith("TKT-") and q.strip()[4:].isdigit():
            conditions.append(Ticket.number == int(q.strip()[4:]))
        elif q.strip().isdigit():
            conditions.append(Ticket.number == int(q.strip()))
        query = query.filter(or_(*conditions))

    total = query.with_entities(func.count(Ticket.id)).scalar() or 0
    rows = (
        query.order_by(Ticket.last_activity_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )

    counts = _message_counts(db, [t.id for t in rows], viewer)
    return TicketListResponse(
        items=[
            _summary(
                t,
                message_count=counts.get(t.id, (0, 0))[0],
                attachment_count=counts.get(t.id, (0, 0))[1],
            )
            for t in rows
        ],
        total=int(total),
        page=page,
        page_size=page_size,
    )


def _message_counts(
    db: Session, ticket_ids: list[str], viewer: User
) -> dict[str, tuple[int, int]]:
    if not ticket_ids:
        return {}
    message_query = select(
        TicketMessage.ticket_id, func.count(TicketMessage.id)
    ).where(TicketMessage.ticket_id.in_(ticket_ids))
    if not viewer.is_staff:
        message_query = message_query.where(TicketMessage.kind != "note")
    messages = dict(db.execute(message_query.group_by(TicketMessage.ticket_id)).all())

    attachment_rows = db.execute(
        select(Attachment.ticket_id, func.count(Attachment.id))
        .where(Attachment.ticket_id.in_(ticket_ids), Attachment.is_inline.is_(False))
        .group_by(Attachment.ticket_id)
    ).all()
    attachments = dict(attachment_rows)

    return {
        ticket_id: (int(messages.get(ticket_id, 0)), int(attachments.get(ticket_id, 0)))
        for ticket_id in ticket_ids
    }


@router.post("", response_model=TicketDetailOut, status_code=status.HTTP_201_CREATED)
def create_ticket(
    payload: TicketCreateRequest,
    request: Request,
    db: DbSession,
    context: CurrentUser,
) -> TicketDetailOut:
    viewer = context.user

    template = None
    if payload.template_id:
        template = db.get(Template, payload.template_id)
        if template is None or not template.is_active:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="Unknown template"
            )
    else:
        template = template_service.get_fallback(db)

    try:
        field_values = template_service.validate_answers(template, payload.field_values)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc

    # Only staff may raise a ticket on somebody else's behalf.
    if payload.requester_email and viewer.is_staff:
        requester = ticket_service.get_or_create_requester(
            db, payload.requester_email, payload.requester_name or ""
        )
    else:
        requester = viewer

    body = payload.body.strip()
    summary = template_service.render_field_summary(template, field_values)
    body_text = f"{body}\n\n{summary}".strip() if summary else body

    ticket, message = ticket_service.create_ticket(
        db,
        subject=payload.subject,
        requester=requester,
        body_text=body_text,
        template=template,
        field_values=field_values,
        priority=payload.priority,
        source="web",
        author=viewer,
        author_email=viewer.email,
        author_name=viewer.display_name,
        message_kind="inbound",
    )
    ticket_service.claim_attachments(
        db, ticket, message, payload.attachment_ids, uploader=viewer
    )

    # Acknowledge by email so the requester has a thread to reply into, unless
    # an agent raised it for themselves.
    if requester.id != viewer.id or not viewer.is_staff:
        ack_body = email_outbound.compose_body(
            ticket,
            f"Your request has been logged as {ticket.key}.\n\n{body}",
        )
        ack = ticket_service.add_message(
            db,
            ticket,
            kind="system",
            body_text=ack_body,
            author=None,
            author_name="Helpdesk",
        )
        email_outbound.queue_message(
            db, ticket, ack, to_email=requester.email, subject=ticket.subject,
            body_text=ack_body,
        )

    audit.record(
        db,
        action="ticket.created",
        actor=viewer,
        object_type="ticket",
        object_id=ticket.id,
        ip_address=sessions.client_ip(request),
        detail={"source": "web", "template": template.slug if template else None},
    )
    db.commit()
    db.refresh(ticket)
    return _detail(_load_ticket(db, ticket.id, viewer), viewer)


@router.get("/{ticket_id}", response_model=TicketDetailOut)
def get_ticket(ticket_id: str, db: DbSession, context: CurrentUser) -> TicketDetailOut:
    return _detail(_load_ticket(db, ticket_id, context.user), context.user)


@router.post("/{ticket_id}/reply", response_model=TicketDetailOut)
def reply(
    ticket_id: str,
    payload: TicketReplyRequest,
    request: Request,
    db: DbSession,
    context: CurrentUser,
) -> TicketDetailOut:
    """Add a reply.

    A staff reply is emailed to the requester; a requester's reply is recorded
    on the ticket and shows up in their own timeline straight away.
    """
    viewer = context.user
    ticket = _load_ticket(db, ticket_id, viewer)

    internal = payload.internal and viewer.is_staff
    kind = "note" if internal else ("outbound" if viewer.is_staff else "inbound")

    body = payload.body.strip()
    outgoing_text = (
        email_outbound.compose_body(ticket, body) if kind == "outbound" else body
    )

    message = ticket_service.add_message(
        db,
        ticket,
        kind=kind,
        body_text=outgoing_text,
        author=viewer,
        author_email=viewer.email,
        author_name=viewer.display_name or viewer.email,
    )
    ticket_service.claim_attachments(
        db, ticket, message, payload.attachment_ids, uploader=viewer
    )

    if kind == "outbound":
        email_outbound.queue_message(
            db,
            ticket,
            message,
            to_email=ticket.requester.email,
            subject=ticket.subject,
            body_text=outgoing_text,
        )

    if payload.set_status and viewer.is_staff:
        ticket_service.set_status(db, ticket, payload.set_status)
    elif kind == "outbound" and ticket.status in ("new",):
        ticket_service.set_status(db, ticket, "pending")

    audit.record(
        db,
        action=f"ticket.reply.{kind}",
        actor=viewer,
        object_type="ticket",
        object_id=ticket.id,
        ip_address=sessions.client_ip(request),
        detail={"attachments": len(payload.attachment_ids)},
    )
    db.commit()
    return _detail(_load_ticket(db, ticket.id, viewer), viewer)


@router.patch("/{ticket_id}", response_model=TicketDetailOut)
def update_ticket(
    ticket_id: str,
    payload: TicketUpdateRequest,
    request: Request,
    db: DbSession,
    context: CurrentUser,
) -> TicketDetailOut:
    """Staff-only edits: status, priority, assignment, template and fields.

    Re-templating is the intended path for an emailed-in ticket: pick the real
    template here and fill in its fields afterwards.
    """
    viewer = context.user
    if not viewer.is_staff:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Only agents can change a ticket"
        )
    ticket = _load_ticket(db, ticket_id, viewer)
    changes: dict[str, object] = {}

    if payload.subject is not None and payload.subject.strip():
        ticket.subject = payload.subject.strip()[:500]
        changes["subject"] = ticket.subject

    if payload.priority:
        ticket.priority = payload.priority
        changes["priority"] = payload.priority

    if payload.assignee_id is not None:
        if payload.assignee_id == "":
            # Assign the relationship, not just the id: the session keeps the
            # loaded object otherwise and the response would show the old value.
            ticket.assignee = None
            changes["assignee"] = None
        else:
            assignee = db.get(User, payload.assignee_id)
            if assignee is None or not assignee.is_staff or not assignee.is_active:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Assignee must be an active agent",
                )
            ticket.assignee = assignee
            changes["assignee"] = assignee.email

    if payload.template_id is not None:
        template = db.get(Template, payload.template_id) if payload.template_id else None
        if payload.template_id and template is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="Unknown template"
            )
        ticket.template = template
        changes["template"] = template.slug if template else None
        # Field answers belong to the old template; drop anything the new one
        # does not define rather than carrying stale keys around.
        if payload.field_values is None:
            ticket.field_values = template_service.validate_answers(
                template, ticket.field_values or {}
            ) if template else {}

    if payload.field_values is not None:
        # ticket.template, not a re-fetch by id: a template change earlier in
        # this request has not been flushed yet, so the id is still the old one.
        template = ticket.template
        try:
            ticket.field_values = template_service.validate_answers(
                template, payload.field_values
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
            ) from exc
        changes["field_values"] = sorted(ticket.field_values)

    if payload.status:
        ticket_service.set_status(db, ticket, payload.status)
        changes["status"] = payload.status

    ticket.last_activity_at = datetime.now(UTC)
    audit.record(
        db,
        action="ticket.updated",
        actor=viewer,
        object_type="ticket",
        object_id=ticket.id,
        ip_address=sessions.client_ip(request),
        detail=changes,
    )
    db.commit()
    return _detail(_load_ticket(db, ticket.id, viewer), viewer)
