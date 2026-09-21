"""Ticket template management."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, Response, status

from app.deps import AdminUser, CurrentUser, DbSession
from app.models import Template
from app.schemas import TemplateOut, TemplateWriteRequest
from app.security import sessions
from app.services import audit
from app.services import templates as template_service

router = APIRouter(prefix="/templates", tags=["templates"])


@router.get("", response_model=list[TemplateOut])
def list_templates(
    db: DbSession,
    context: CurrentUser,
    include_inactive: bool = False,
) -> list[Template]:
    query = db.query(Template)
    # Only admins have a reason to see retired templates.
    if not include_inactive or context.user.role != "admin":
        query = query.filter(Template.is_active.is_(True))
    return query.order_by(Template.sort_order, Template.name).all()


@router.post("", response_model=TemplateOut, status_code=status.HTTP_201_CREATED)
def create_template(
    payload: TemplateWriteRequest,
    request: Request,
    db: DbSession,
    context: AdminUser,
) -> Template:
    if db.query(Template).filter(Template.slug == payload.slug).count():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="A template with that slug exists"
        )
    template = Template(**_clean(payload))
    db.add(template)
    db.flush()
    _enforce_single_fallback(db, template)
    audit.record(
        db,
        action="template.created",
        actor=context.user,
        object_type="template",
        object_id=template.id,
        ip_address=sessions.client_ip(request),
        detail={"slug": template.slug},
    )
    db.commit()
    return template


@router.put("/{template_id}", response_model=TemplateOut)
def update_template(
    template_id: str,
    payload: TemplateWriteRequest,
    request: Request,
    db: DbSession,
    context: AdminUser,
) -> Template:
    template = db.get(Template, template_id)
    if template is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Template not found")

    clash = (
        db.query(Template)
        .filter(Template.slug == payload.slug, Template.id != template_id)
        .count()
    )
    if clash:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="A template with that slug exists"
        )

    for key, value in _clean(payload).items():
        setattr(template, key, value)
    db.flush()
    _enforce_single_fallback(db, template)
    audit.record(
        db,
        action="template.updated",
        actor=context.user,
        object_type="template",
        object_id=template.id,
        ip_address=sessions.client_ip(request),
        detail={"slug": template.slug},
    )
    db.commit()
    return template


@router.delete("/{template_id}", status_code=status.HTTP_204_NO_CONTENT)
def retire_template(
    template_id: str,
    request: Request,
    db: DbSession,
    context: AdminUser,
) -> Response:
    """Deactivate rather than delete, so existing tickets keep their template."""
    template = db.get(Template, template_id)
    if template is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Template not found")
    if template.is_fallback:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Make another template the fallback before retiring this one",
        )
    template.is_active = False
    audit.record(
        db,
        action="template.retired",
        actor=context.user,
        object_type="template",
        object_id=template.id,
        ip_address=sessions.client_ip(request),
    )
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def _clean(payload: TemplateWriteRequest) -> dict:
    try:
        fields = template_service.normalise_field_spec(payload.fields)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    data = payload.model_dump()
    data["fields"] = fields
    return data


def _enforce_single_fallback(db: DbSession, template: Template) -> None:
    """Exactly one template can be the email fallback."""
    if not template.is_fallback:
        return
    db.query(Template).filter(
        Template.id != template.id, Template.is_fallback.is_(True)
    ).update({"is_fallback": False}, synchronize_session=False)
    db.flush()
