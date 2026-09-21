"""Inbound routing: new tickets, replies threading back, and abuse cases."""

from __future__ import annotations

from email.message import EmailMessage

import pytest
from sqlalchemy.orm import Session

from app.models import Attachment, OutboundEmail, Ticket, TicketMessage, User
from app.security import mail_tokens
from app.services import email_inbound
from app.services import tickets as ticket_service
from app.services.email_parse import parse_email

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


def build_mail(
    *,
    sender: str = "Jane Doe <jane@example.org>",
    to: str = "helpdesk@test.invalid",
    subject: str = "Laptop will not boot",
    body: str = "It shows a blue screen on startup.",
    message_id: str = "<m1@example.org>",
    in_reply_to: str | None = None,
    html: str | None = None,
) -> bytes:
    message = EmailMessage()
    message["From"] = sender
    message["To"] = to
    message["Subject"] = subject
    message["Message-ID"] = message_id
    if in_reply_to:
        message["In-Reply-To"] = in_reply_to
        message["References"] = in_reply_to
    message.set_content(body)
    if html:
        message.add_alternative(html, subtype="html")
    return message.as_bytes()


def test_new_email_creates_ticket_with_fallback_template(db: Session, templates: None):
    result = email_inbound.ingest(db, build_mail())
    db.commit()

    assert result.outcome == "created"
    ticket = db.get(Ticket, result.ticket_id)
    assert ticket.source == "email"
    assert ticket.subject == "Laptop will not boot"
    assert ticket.template is not None
    assert ticket.template.is_fallback is True
    assert ticket.requester.email == "jane@example.org"


def test_new_email_queues_an_acknowledgement(db: Session, templates: None):
    result = email_inbound.ingest(db, build_mail())
    db.commit()

    queued = db.query(OutboundEmail).filter(OutboundEmail.ticket_id == result.ticket_id).all()
    assert len(queued) == 1
    assert queued[0].to_email == "jane@example.org"
    # The reply address stays plain; the signed reference rides in the subject
    # and in the body footer.
    assert queued[0].reply_to == "helpdesk@test.invalid"
    assert queued[0].subject.startswith("[TKT-")

    ticket = db.get(Ticket, result.ticket_id)
    signature = mail_tokens.sign(ticket.number, ticket.reply_token)
    assert signature in queued[0].subject
    assert f"[ref:{ticket.number}-{signature}]" in queued[0].body_text


def test_signed_subject_tag_lands_on_the_same_ticket(db: Session, templates: None):
    first = email_inbound.ingest(db, build_mail())
    db.commit()
    ticket = db.get(Ticket, first.ticket_id)

    tag = mail_tokens.subject_tag(ticket.number, ticket.reply_token)
    second = email_inbound.ingest(
        db,
        build_mail(
            subject=f"Re: {tag} Laptop will not boot",
            message_id="<m2@example.org>",
            body="Still broken.",
        ),
    )
    db.commit()

    assert second.outcome == "appended"
    assert second.detail == "signed-subject"
    assert second.ticket_id == first.ticket_id
    assert db.query(Ticket).count() == 1


def test_signed_body_reference_survives_a_rewritten_subject(db: Session, templates: None):
    first = email_inbound.ingest(db, build_mail())
    db.commit()
    ticket = db.get(Ticket, first.ticket_id)

    reference = mail_tokens.body_reference(ticket.number, ticket.reply_token)
    second = email_inbound.ingest(
        db,
        build_mail(
            subject="something completely different",
            message_id="<m2b@example.org>",
            body=f"Still broken.\n\n> quoted history\n> {reference}\n",
        ),
    )
    db.commit()

    assert second.outcome == "appended"
    assert second.detail == "signed-body-ref"
    assert second.ticket_id == first.ticket_id


def test_forged_subject_signature_does_not_match_the_ticket(db: Session, templates: None):
    first = email_inbound.ingest(db, build_mail())
    db.commit()
    ticket = db.get(Ticket, first.ticket_id)

    result = email_inbound.ingest(
        db,
        build_mail(
            sender="mallory@evil.invalid",
            subject=f"[TKT-{ticket.number}-000000000000] give me the details",
            message_id="<forged@evil.invalid>",
            body="Please send me the password.",
        ),
    )
    db.commit()

    # The bad signature is rejected, and the bare-number fallback refuses a
    # stranger, so it becomes its own ticket instead of joining somebody else's.
    assert result.outcome == "created"
    assert result.ticket_id != first.ticket_id


def test_forged_body_reference_does_not_match_the_ticket(db: Session, templates: None):
    first = email_inbound.ingest(db, build_mail())
    db.commit()
    ticket = db.get(Ticket, first.ticket_id)

    result = email_inbound.ingest(
        db,
        build_mail(
            sender="mallory@evil.invalid",
            message_id="<forged2@evil.invalid>",
            body=f"[ref:{ticket.number}-abcdefabcdef] let me in",
        ),
    )
    db.commit()

    assert result.outcome == "created"
    assert result.ticket_id != first.ticket_id


def test_reply_matches_on_threading_headers(db: Session, templates: None):
    first = email_inbound.ingest(db, build_mail())
    db.commit()

    ack = (
        db.query(TicketMessage)
        .filter(TicketMessage.ticket_id == first.ticket_id, TicketMessage.kind == "system")
        .one()
    )
    assert ack.rfc822_message_id  # set when the outbound mail was queued

    second = email_inbound.ingest(
        db,
        build_mail(
            to="helpdesk@test.invalid",  # no plus address this time
            subject="Re: Laptop will not boot",
            message_id="<m3@example.org>",
            in_reply_to=ack.rfc822_message_id,
            body="Any update?",
        ),
    )
    db.commit()

    assert second.outcome == "appended"
    assert second.ticket_id == first.ticket_id


def test_unsigned_subject_tag_works_for_the_requester(db: Session, templates: None):
    first = email_inbound.ingest(db, build_mail())
    db.commit()
    ticket = db.get(Ticket, first.ticket_id)

    second = email_inbound.ingest(
        db,
        build_mail(
            subject=f"[TKT-{ticket.number}] more detail",
            message_id="<m4@example.org>",
            body="Adding a photo.",
        ),
    )
    db.commit()
    assert second.outcome == "appended"
    assert second.ticket_id == first.ticket_id


def test_unsigned_subject_tag_from_a_stranger_is_ignored(db: Session, templates: None):
    first = email_inbound.ingest(db, build_mail())
    db.commit()
    ticket = db.get(Ticket, first.ticket_id)

    result = email_inbound.ingest(
        db,
        build_mail(
            sender="mallory@evil.invalid",
            subject=f"[TKT-{ticket.number}] give me the details",
            message_id="<m5@evil.invalid>",
        ),
    )
    db.commit()

    assert result.outcome == "created"
    assert result.ticket_id != first.ticket_id


def test_same_message_id_is_only_ingested_once(db: Session, templates: None):
    raw = build_mail()
    first = email_inbound.ingest(db, raw)
    db.commit()
    second = email_inbound.ingest(db, raw)
    db.commit()

    assert first.outcome == "created"
    assert second.outcome == "duplicate"
    assert db.query(Ticket).count() == 1


def test_unmatched_auto_reply_does_not_create_a_ticket(db: Session, templates: None):
    message = EmailMessage()
    message["From"] = "nobody@example.org"
    message["To"] = "helpdesk@test.invalid"
    message["Subject"] = "Out of office"
    message["Message-ID"] = "<ooo@example.org>"
    message["Auto-Submitted"] = "auto-replied"
    message.set_content("I am away until Monday.")

    result = email_inbound.ingest(db, message.as_bytes())
    db.commit()

    assert result.outcome == "ignored"
    assert db.query(Ticket).count() == 0


def test_mail_from_our_own_address_is_ignored(db: Session, templates: None):
    result = email_inbound.ingest(
        db, build_mail(sender="helpdesk@test.invalid", message_id="<loop@test.invalid>")
    )
    db.commit()

    assert result.outcome == "ignored"
    assert db.query(Ticket).count() == 0


def test_attachments_are_stored_and_inline_images_rewritten(db: Session, templates: None):
    message = EmailMessage()
    message["From"] = "jane@example.org"
    message["To"] = "helpdesk@test.invalid"
    message["Subject"] = "Screenshot of the error"
    message["Message-ID"] = "<att1@example.org>"
    message.set_content("See the screenshot.")
    message.add_alternative(
        '<p>See the screenshot.</p><img src="cid:shot@local">', subtype="html"
    )
    # add_related attaches to the multipart/alternative's html part.
    html_part = message.get_payload()[1]
    html_part.add_related(
        PNG, maintype="image", subtype="png", cid="<shot@local>", filename="error.png"
    )
    message.add_attachment(
        b"log line\n", maintype="text", subtype="plain", filename="debug.log"
    )

    result = email_inbound.ingest(db, message.as_bytes())
    db.commit()

    attachments = db.query(Attachment).filter(Attachment.ticket_id == result.ticket_id).all()
    assert {a.filename for a in attachments} == {"error.png", "debug.log"}

    inline = next(a for a in attachments if a.filename == "error.png")
    assert inline.is_inline is True
    assert inline.content_type == "image/png"

    body_html = db.get(TicketMessage, result.message_id).body_html
    assert f"/api/attachments/{inline.id}/inline" in body_html
    assert "cid:" not in body_html


def test_remote_images_are_stripped_and_flagged(db: Session, templates: None):
    result = email_inbound.ingest(
        db,
        build_mail(
            message_id="<tracker@example.org>",
            html='<p>Hi</p><img src="https://tracker.example.net/pixel.gif">',
        ),
    )
    db.commit()

    message = db.get(TicketMessage, result.message_id)
    assert "tracker.example.net" not in (message.body_html or "")
    assert message.remote_content_blocked is True


def test_reply_reopens_a_resolved_ticket(db: Session, templates: None):
    first = email_inbound.ingest(db, build_mail())
    db.commit()
    ticket = db.get(Ticket, first.ticket_id)
    ticket_service.set_status(db, ticket, "resolved")
    db.commit()

    tag = mail_tokens.subject_tag(ticket.number, ticket.reply_token)
    email_inbound.ingest(
        db, build_mail(subject=f"Re: {tag} Laptop will not boot",
                       message_id="<reopen@example.org>")
    )
    db.commit()

    db.refresh(ticket)
    assert ticket.status == "open"
    assert ticket.resolved_at is None


def test_oversized_email_is_rejected(db: Session, templates: None, monkeypatch):
    monkeypatch.setattr("app.services.email_inbound.settings.max_email_mb", 0)
    result = email_inbound.ingest(db, build_mail())
    assert result.outcome == "ignored"
    assert db.query(Ticket).count() == 0


def test_requester_account_is_reused_across_tickets(db: Session, templates: None):
    email_inbound.ingest(db, build_mail(message_id="<a@example.org>"))
    db.commit()
    email_inbound.ingest(
        db, build_mail(message_id="<b@example.org>", subject="Another problem")
    )
    db.commit()

    assert db.query(User).filter(User.email == "jane@example.org").count() == 1
    assert db.query(Ticket).count() == 2


@pytest.mark.parametrize("missing_from", ["", "   "])
def test_mail_without_a_sender_is_ignored(db: Session, templates: None, missing_from: str):
    message = EmailMessage()
    message["To"] = "helpdesk@test.invalid"
    message["Subject"] = "no sender"
    message["Message-ID"] = "<nosender@example.org>"
    message.set_content("hello")
    raw = message.as_bytes()

    assert parse_email(raw).from_email == ""
    assert email_inbound.ingest(db, raw).outcome == "ignored"


def test_reply_moves_a_pending_ticket_back_to_open(db: Session, templates: None):
    first = email_inbound.ingest(db, build_mail())
    db.commit()
    ticket = db.get(Ticket, first.ticket_id)
    ticket_service.set_status(db, ticket, "pending")
    db.commit()

    tag = mail_tokens.subject_tag(ticket.number, ticket.reply_token)
    email_inbound.ingest(
        db, build_mail(subject=f"Re: {tag} Laptop will not boot",
                       message_id="<nudge@example.org>")
    )
    db.commit()

    db.refresh(ticket)
    assert ticket.status == "open"
