"""End-to-end API behaviour: sign-in, MFA, CSRF, permissions, replies."""

from __future__ import annotations

from collections.abc import Iterator

import pyotp
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.main import app
from app.models import OutboundEmail, Ticket, TicketMessage, User
from app.security import passwords, totp
from app.security.crypto import encrypt_secret
from app.services import templates as template_service

AGENT_PASSWORD = "agent-passphrase-2026!"
USER_PASSWORD = "requester-passphrase-2026!"


@pytest.fixture()
def client(db: Session) -> Iterator[TestClient]:
    template_service.seed_defaults(db)
    db.commit()
    with TestClient(app) as test_client:
        yield test_client


def make_user(
    db: Session,
    email: str,
    role: str,
    password: str,
    *,
    mfa_secret: str | None = None,
) -> User:
    user = User(
        email=email,
        display_name=email.split("@")[0],
        role=role,
        is_active=True,
        password_hash=passwords.hash_password(password),
    )
    if mfa_secret:
        user.mfa_enabled = True
        user.totp_secret_encrypted = encrypt_secret(mfa_secret)
    db.add(user)
    db.commit()
    return user


def sign_in(client: TestClient, email: str, password: str) -> dict:
    response = client.post("/api/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    body = response.json()
    if body.get("csrf_token"):
        client.headers["X-CSRF-Token"] = body["csrf_token"]
    return body


class TestAuthentication:
    def test_unauthenticated_requests_are_rejected(self, client: TestClient):
        assert client.get("/api/tickets").status_code == 401

    def test_health_is_public(self, client: TestClient):
        response = client.get("/api/health")
        assert response.status_code == 200
        assert response.json()["database"] == "ok"

    def test_login_without_mfa_authenticates(self, client: TestClient, db: Session):
        # MFA enforcement only applies to staff; this one is a requester.
        make_user(db, "jane@example.org", "requester", USER_PASSWORD)
        body = sign_in(client, "jane@example.org", USER_PASSWORD)

        assert body["status"] == "authenticated"
        assert body["user"]["email"] == "jane@example.org"
        assert client.get("/api/tickets").status_code == 200

    def test_wrong_password_is_rejected_with_a_generic_message(
        self, client: TestClient, db: Session
    ):
        make_user(db, "jane@example.org", "requester", USER_PASSWORD)
        response = client.post(
            "/api/auth/login", json={"email": "jane@example.org", "password": "nope-nope-nope"}
        )
        assert response.status_code == 401
        assert response.json()["detail"] == "Email or password is incorrect"

    def test_unknown_account_gives_the_same_message(self, client: TestClient):
        response = client.post(
            "/api/auth/login", json={"email": "ghost@example.org", "password": "whatever-12345"}
        )
        assert response.status_code == 401
        assert response.json()["detail"] == "Email or password is incorrect"

    def test_inactive_account_cannot_sign_in(self, client: TestClient, db: Session):
        user = make_user(db, "gone@example.org", "requester", USER_PASSWORD)
        user.is_active = False
        db.commit()
        response = client.post(
            "/api/auth/login", json={"email": "gone@example.org", "password": USER_PASSWORD}
        )
        assert response.status_code == 401

    def test_repeated_failures_are_rate_limited(self, client: TestClient, db: Session):
        make_user(db, "jane@example.org", "requester", USER_PASSWORD)
        statuses = [
            client.post(
                "/api/auth/login", json={"email": "jane@example.org", "password": "bad-password-x"}
            ).status_code
            for _ in range(12)
        ]
        assert 429 in statuses

    def test_logout_invalidates_the_session(self, client: TestClient, db: Session):
        make_user(db, "jane@example.org", "requester", USER_PASSWORD)
        sign_in(client, "jane@example.org", USER_PASSWORD)
        assert client.post("/api/auth/logout").status_code == 200
        assert client.get("/api/tickets").status_code == 401


class TestMfa:
    def test_staff_must_enrol_before_reaching_tickets(self, client: TestClient, db: Session):
        make_user(db, "agent@example.org", "agent", AGENT_PASSWORD)
        body = sign_in(client, "agent@example.org", AGENT_PASSWORD)

        assert body["status"] == "mfa_enrolment_required"
        assert client.get("/api/tickets").status_code == 401

    def test_enrolment_then_access(self, client: TestClient, db: Session):
        make_user(db, "agent@example.org", "agent", AGENT_PASSWORD)
        sign_in(client, "agent@example.org", AGENT_PASSWORD)

        start = client.post("/api/auth/mfa/enrol/start")
        assert start.status_code == 200
        secret = start.json()["secret"]
        assert start.json()["otpauth_uri"].startswith("otpauth://")

        confirm = client.post(
            "/api/auth/mfa/enrol/confirm", json={"code": pyotp.TOTP(secret).now()}
        )
        assert confirm.status_code == 200
        codes = confirm.json()["recovery_codes"]
        assert len(codes) == totp.RECOVERY_CODE_COUNT

        # The session token rotated, so refresh the CSRF header.
        me = client.get("/api/auth/me").json()
        client.headers["X-CSRF-Token"] = me["csrf_token"]
        assert client.get("/api/tickets").status_code == 200

    def test_enrolled_staff_must_supply_a_code(self, client: TestClient, db: Session):
        secret = pyotp.random_base32()
        make_user(db, "agent@example.org", "agent", AGENT_PASSWORD, mfa_secret=secret)

        body = sign_in(client, "agent@example.org", AGENT_PASSWORD)
        assert body["status"] == "mfa_required"
        assert client.get("/api/tickets").status_code == 401

        wrong = client.post("/api/auth/mfa/verify", json={"code": "000000"})
        assert wrong.status_code == 401

        ok = client.post("/api/auth/mfa/verify", json={"code": pyotp.TOTP(secret).now()})
        assert ok.status_code == 200
        client.headers["X-CSRF-Token"] = ok.json()["csrf_token"]
        assert client.get("/api/tickets").status_code == 200

    def test_a_recovery_code_works_once(self, client: TestClient, db: Session):
        secret = pyotp.random_base32()
        user = make_user(db, "agent@example.org", "agent", AGENT_PASSWORD, mfa_secret=secret)

        from app.models import RecoveryCode

        code = "aaaa-bbbb-cccc-dddd"
        db.add(RecoveryCode(user_id=user.id, code_hash=totp.hash_recovery_code(code)))
        db.commit()

        sign_in(client, "agent@example.org", AGENT_PASSWORD)
        first = client.post("/api/auth/mfa/verify", json={"code": code})
        assert first.status_code == 200

        client.post("/api/auth/logout")
        sign_in(client, "agent@example.org", AGENT_PASSWORD)
        second = client.post("/api/auth/mfa/verify", json={"code": code})
        assert second.status_code == 401


class TestCsrf:
    def test_state_changing_request_without_the_header_is_refused(
        self, client: TestClient, db: Session
    ):
        make_user(db, "jane@example.org", "requester", USER_PASSWORD)
        sign_in(client, "jane@example.org", USER_PASSWORD)
        client.headers.pop("X-CSRF-Token", None)

        response = client.post(
            "/api/tickets", json={"subject": "No CSRF token", "body": "should fail"}
        )
        assert response.status_code == 403

    def test_a_wrong_token_is_refused(self, client: TestClient, db: Session):
        make_user(db, "jane@example.org", "requester", USER_PASSWORD)
        sign_in(client, "jane@example.org", USER_PASSWORD)
        client.headers["X-CSRF-Token"] = "not-the-right-token"

        response = client.post(
            "/api/tickets", json={"subject": "Bad CSRF token", "body": "should fail"}
        )
        assert response.status_code == 403

    def test_reads_do_not_need_the_header(self, client: TestClient, db: Session):
        make_user(db, "jane@example.org", "requester", USER_PASSWORD)
        sign_in(client, "jane@example.org", USER_PASSWORD)
        client.headers.pop("X-CSRF-Token", None)
        assert client.get("/api/tickets").status_code == 200


class TestSecurityHeaders:
    def test_headers_are_present(self, client: TestClient):
        headers = client.get("/api/health").headers
        assert headers["X-Content-Type-Options"] == "nosniff"
        assert headers["X-Frame-Options"] == "DENY"
        assert headers["Referrer-Policy"] == "no-referrer"
        assert "frame-ancestors 'none'" in headers["Content-Security-Policy"]

    def test_api_responses_are_not_cached(self, client: TestClient):
        assert client.get("/api/health").headers["Cache-Control"] == "no-store"


class TestTicketsApi:
    def test_requester_creates_a_ticket_from_a_template(self, client: TestClient, db: Session):
        make_user(db, "jane@example.org", "requester", USER_PASSWORD)
        sign_in(client, "jane@example.org", USER_PASSWORD)

        template = next(
            t for t in client.get("/api/templates").json() if t["slug"] == "device-problem"
        )
        response = client.post(
            "/api/tickets",
            json={
                "subject": "Laptop will not boot",
                "body": "Blue screen on startup.",
                "template_id": template["id"],
                "field_values": {"device": "Laptop", "what_happened": "It beeps and stops"},
            },
        )
        assert response.status_code == 201, response.text
        body = response.json()
        assert body["key"].startswith("TKT-")
        assert body["field_values"]["device"] == "Laptop"
        assert body["template_name"] == "Something is not working"

    def test_required_template_fields_are_enforced(self, client: TestClient, db: Session):
        make_user(db, "jane@example.org", "requester", USER_PASSWORD)
        sign_in(client, "jane@example.org", USER_PASSWORD)
        template = next(
            t for t in client.get("/api/templates").json() if t["slug"] == "device-problem"
        )

        response = client.post(
            "/api/tickets",
            json={
                "subject": "Missing fields",
                "body": "...",
                "template_id": template["id"],
                "field_values": {},
            },
        )
        assert response.status_code == 422
        assert "required" in response.json()["detail"]

    def test_requesters_cannot_see_other_peoples_tickets(self, client: TestClient, db: Session):
        make_user(db, "jane@example.org", "requester", USER_PASSWORD)
        make_user(db, "bob@example.org", "requester", USER_PASSWORD)

        sign_in(client, "jane@example.org", USER_PASSWORD)
        ticket_id = client.post(
            "/api/tickets", json={"subject": "Private", "body": "secret detail"}
        ).json()["id"]
        client.post("/api/auth/logout")

        sign_in(client, "bob@example.org", USER_PASSWORD)
        assert client.get(f"/api/tickets/{ticket_id}").status_code == 404
        assert client.get("/api/tickets").json()["total"] == 0

    def test_agent_reply_queues_an_email_to_the_requester(
        self, client: TestClient, db: Session
    ):
        secret = pyotp.random_base32()
        make_user(db, "agent@example.org", "agent", AGENT_PASSWORD, mfa_secret=secret)
        make_user(db, "jane@example.org", "requester", USER_PASSWORD)

        sign_in(client, "jane@example.org", USER_PASSWORD)
        ticket_id = client.post(
            "/api/tickets", json={"subject": "Need help", "body": "Something is wrong"}
        ).json()["id"]
        client.post("/api/auth/logout")

        sign_in(client, "agent@example.org", AGENT_PASSWORD)
        verified = client.post("/api/auth/mfa/verify", json={"code": pyotp.TOTP(secret).now()})
        client.headers["X-CSRF-Token"] = verified.json()["csrf_token"]

        response = client.post(
            f"/api/tickets/{ticket_id}/reply",
            json={"body": "Have you tried restarting it?", "set_status": "pending"},
        )
        assert response.status_code == 200
        assert response.json()["status"] == "pending"

        queued = (
            db.query(OutboundEmail)
            .filter(OutboundEmail.ticket_id == ticket_id, OutboundEmail.to_email == "jane@example.org")
            .all()
        )
        # One acknowledgement for the new ticket, one for the agent's reply.
        assert len(queued) == 2
        assert "restarting it" in queued[-1].body_text

    def test_internal_notes_are_hidden_from_the_requester(
        self, client: TestClient, db: Session
    ):
        secret = pyotp.random_base32()
        make_user(db, "agent@example.org", "agent", AGENT_PASSWORD, mfa_secret=secret)
        make_user(db, "jane@example.org", "requester", USER_PASSWORD)

        sign_in(client, "jane@example.org", USER_PASSWORD)
        ticket_id = client.post(
            "/api/tickets", json={"subject": "Need help", "body": "Something is wrong"}
        ).json()["id"]
        client.post("/api/auth/logout")

        sign_in(client, "agent@example.org", AGENT_PASSWORD)
        verified = client.post("/api/auth/mfa/verify", json={"code": pyotp.TOTP(secret).now()})
        client.headers["X-CSRF-Token"] = verified.json()["csrf_token"]
        client.post(
            f"/api/tickets/{ticket_id}/reply",
            json={"body": "Probably a failing disk, do not tell them yet", "internal": True},
        )
        client.post("/api/auth/logout")

        sign_in(client, "jane@example.org", USER_PASSWORD)
        detail = client.get(f"/api/tickets/{ticket_id}").json()
        bodies = " ".join(m["body_text"] for m in detail["messages"])
        assert "failing disk" not in bodies

        # No email went out for the note either.
        assert (
            db.query(OutboundEmail)
            .filter(OutboundEmail.ticket_id == ticket_id)
            .count()
            == 1
        )

    def test_requester_reply_is_visible_in_their_own_timeline(
        self, client: TestClient, db: Session
    ):
        make_user(db, "jane@example.org", "requester", USER_PASSWORD)
        sign_in(client, "jane@example.org", USER_PASSWORD)

        ticket_id = client.post(
            "/api/tickets", json={"subject": "Need help", "body": "Something is wrong"}
        ).json()["id"]
        client.post(f"/api/tickets/{ticket_id}/reply", json={"body": "Here is more detail"})

        detail = client.get(f"/api/tickets/{ticket_id}").json()
        bodies = [m["body_text"] for m in detail["messages"]]
        assert any("more detail" in b for b in bodies)

    def test_requesters_cannot_change_a_ticket(self, client: TestClient, db: Session):
        make_user(db, "jane@example.org", "requester", USER_PASSWORD)
        sign_in(client, "jane@example.org", USER_PASSWORD)
        ticket_id = client.post(
            "/api/tickets", json={"subject": "Need help", "body": "Something is wrong"}
        ).json()["id"]

        response = client.patch(f"/api/tickets/{ticket_id}", json={"priority": "urgent"})
        assert response.status_code == 403

    def test_agent_can_retemplate_an_emailed_ticket(self, client: TestClient, db: Session):
        secret = pyotp.random_base32()
        make_user(db, "agent@example.org", "agent", AGENT_PASSWORD, mfa_secret=secret)

        from app.services import email_inbound
        from tests.test_email_routing import build_mail

        result = email_inbound.ingest(db, build_mail())
        db.commit()

        sign_in(client, "agent@example.org", AGENT_PASSWORD)
        verified = client.post("/api/auth/mfa/verify", json={"code": pyotp.TOTP(secret).now()})
        client.headers["X-CSRF-Token"] = verified.json()["csrf_token"]

        template = next(
            t for t in client.get("/api/templates").json() if t["slug"] == "device-problem"
        )
        response = client.patch(
            f"/api/tickets/{result.ticket_id}",
            json={
                "template_id": template["id"],
                "field_values": {"device": "Laptop", "what_happened": "Reported by email"},
                "priority": "high",
            },
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["template_name"] == "Something is not working"
        assert body["field_values"]["device"] == "Laptop"
        assert body["priority"] == "high"


class TestAdminApi:
    def _admin_client(self, client: TestClient, db: Session) -> str:
        secret = pyotp.random_base32()
        make_user(db, "admin@example.org", "admin", AGENT_PASSWORD, mfa_secret=secret)
        sign_in(client, "admin@example.org", AGENT_PASSWORD)
        verified = client.post("/api/auth/mfa/verify", json={"code": pyotp.TOTP(secret).now()})
        client.headers["X-CSRF-Token"] = verified.json()["csrf_token"]
        return secret

    def test_agents_cannot_reach_admin_endpoints(self, client: TestClient, db: Session):
        secret = pyotp.random_base32()
        make_user(db, "agent@example.org", "agent", AGENT_PASSWORD, mfa_secret=secret)
        sign_in(client, "agent@example.org", AGENT_PASSWORD)
        verified = client.post("/api/auth/mfa/verify", json={"code": pyotp.TOTP(secret).now()})
        client.headers["X-CSRF-Token"] = verified.json()["csrf_token"]

        assert client.get("/api/audit").status_code == 403

    def test_admin_can_create_a_template_and_it_appears(self, client: TestClient, db: Session):
        self._admin_client(client, db)
        response = client.post(
            "/api/templates",
            json={
                "slug": "vpn-access",
                "name": "VPN access",
                "description": "Request VPN",
                "fields": [
                    {"key": "reason", "label": "Why?", "type": "textarea", "required": True}
                ],
            },
        )
        assert response.status_code == 201, response.text
        slugs = {t["slug"] for t in client.get("/api/templates").json()}
        assert "vpn-access" in slugs

    def test_invalid_template_field_is_rejected(self, client: TestClient, db: Session):
        self._admin_client(client, db)
        response = client.post(
            "/api/templates",
            json={
                "slug": "broken",
                "name": "Broken",
                "fields": [{"key": "x", "label": "X", "type": "select", "options": []}],
            },
        )
        assert response.status_code == 422

    def test_last_admin_cannot_be_demoted(self, client: TestClient, db: Session):
        self._admin_client(client, db)
        admin = db.query(User).filter(User.email == "admin@example.org").one()
        response = client.patch(f"/api/users/{admin.id}", json={"role": "agent"})
        assert response.status_code == 400

    def test_mail_health_reports_the_queue(self, client: TestClient, db: Session):
        self._admin_client(client, db)
        body = client.get("/api/mail/health").json()
        assert body["outbound_configured"] is True
        assert "queued" in body


class TestUploads:
    def test_upload_then_attach_to_a_ticket(self, client: TestClient, db: Session):
        make_user(db, "jane@example.org", "requester", USER_PASSWORD)
        sign_in(client, "jane@example.org", USER_PASSWORD)

        upload = client.post(
            "/api/uploads",
            files={"file": ("screenshot.png", b"\x89PNG\r\n\x1a\n" + b"0" * 40, "image/png")},
        )
        assert upload.status_code == 201, upload.text
        attachment_id = upload.json()["id"]

        created = client.post(
            "/api/tickets",
            json={
                "subject": "With a screenshot",
                "body": "See attached",
                "attachment_ids": [attachment_id],
            },
        )
        assert created.status_code == 201
        attachments = created.json()["messages"][0]["attachments"]
        assert [a["filename"] for a in attachments] == ["screenshot.png"]

    def test_downloads_are_forced_as_attachments(self, client: TestClient, db: Session):
        make_user(db, "jane@example.org", "requester", USER_PASSWORD)
        sign_in(client, "jane@example.org", USER_PASSWORD)

        upload = client.post(
            "/api/uploads",
            files={"file": ("page.html", b"<script>alert(1)</script>", "text/html")},
        )
        attachment_id = upload.json()["id"]

        response = client.get(f"/api/attachments/{attachment_id}")
        assert response.status_code == 200
        assert response.headers["Content-Type"].startswith("application/octet-stream")
        assert response.headers["Content-Disposition"].startswith("attachment;")

    def test_another_user_cannot_read_an_unclaimed_upload(
        self, client: TestClient, db: Session
    ):
        make_user(db, "jane@example.org", "requester", USER_PASSWORD)
        make_user(db, "bob@example.org", "requester", USER_PASSWORD)

        sign_in(client, "jane@example.org", USER_PASSWORD)
        attachment_id = client.post(
            "/api/uploads", files={"file": ("notes.txt", b"private", "text/plain")}
        ).json()["id"]
        client.post("/api/auth/logout")

        sign_in(client, "bob@example.org", USER_PASSWORD)
        assert client.get(f"/api/attachments/{attachment_id}").status_code == 404

    def test_empty_upload_is_rejected(self, client: TestClient, db: Session):
        make_user(db, "jane@example.org", "requester", USER_PASSWORD)
        sign_in(client, "jane@example.org", USER_PASSWORD)
        response = client.post(
            "/api/uploads", files={"file": ("empty.txt", b"", "text/plain")}
        )
        assert response.status_code == 400


class TestEmailRoundTrip:
    def test_agent_reply_can_be_answered_by_email_onto_the_same_ticket(
        self, client: TestClient, db: Session
    ):
        """The whole loop: GUI reply out, customer reply back, one ticket."""
        secret = pyotp.random_base32()
        make_user(db, "agent@example.org", "agent", AGENT_PASSWORD, mfa_secret=secret)

        from app.security import mail_tokens
        from app.services import email_inbound
        from tests.test_email_routing import build_mail

        created = email_inbound.ingest(db, build_mail())
        db.commit()

        sign_in(client, "agent@example.org", AGENT_PASSWORD)
        verified = client.post("/api/auth/mfa/verify", json={"code": pyotp.TOTP(secret).now()})
        client.headers["X-CSRF-Token"] = verified.json()["csrf_token"]
        client.post(
            f"/api/tickets/{created.ticket_id}/reply",
            json={"body": "Could you send a photo of the screen?"},
        )

        ticket = db.get(Ticket, created.ticket_id)
        db.refresh(ticket)
        tag = mail_tokens.subject_tag(ticket.number, ticket.reply_token)
        answer = email_inbound.ingest(
            db,
            build_mail(
                subject=f"Re: {tag} Laptop will not boot",
                message_id="<customer-reply@example.org>",
                body="Photo attached, it says INACCESSIBLE_BOOT_DEVICE.",
            ),
        )
        db.commit()

        assert answer.outcome == "appended"
        assert answer.ticket_id == created.ticket_id
        assert db.query(Ticket).count() == 1

        detail = client.get(f"/api/tickets/{created.ticket_id}").json()
        bodies = " ".join(m["body_text"] for m in detail["messages"])
        assert "INACCESSIBLE_BOOT_DEVICE" in bodies
        assert db.query(TicketMessage).filter(TicketMessage.kind == "outbound").count() == 1
