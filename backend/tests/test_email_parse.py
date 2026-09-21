"""Parsing of real-world-shaped emails."""

from __future__ import annotations

from email.message import EmailMessage

from app.services.email_parse import parse_email, strip_quoted_reply


def _build(**kwargs) -> EmailMessage:
    message = EmailMessage()
    message["From"] = kwargs.get("from_", "Jane Doe <jane@example.org>")
    message["To"] = kwargs.get("to", "helpdesk@test.invalid")
    message["Subject"] = kwargs.get("subject", "Printer is on fire")
    message["Message-ID"] = kwargs.get("message_id", "<abc123@example.org>")
    message.set_content(kwargs.get("body", "It started smoking this morning."))
    return message


def test_parses_basic_headers_and_body():
    parsed = parse_email(_build().as_bytes())

    assert parsed.from_email == "jane@example.org"
    assert parsed.from_name == "Jane Doe"
    assert parsed.subject == "Printer is on fire"
    assert parsed.message_id == "<abc123@example.org>"
    assert "smoking" in parsed.text_body
    assert parsed.attachments == []


def test_message_id_is_normalised():
    message = _build(message_id="bare-id@example.org")
    parsed = parse_email(message.as_bytes())
    assert parsed.message_id == "<bare-id@example.org>"


def test_html_alternative_is_captured_and_text_derived():
    message = EmailMessage()
    message["From"] = "jane@example.org"
    message["Subject"] = "HTML only"
    message["Message-ID"] = "<html1@example.org>"
    message.set_content("<p>Hello <b>there</b></p>", subtype="html")

    parsed = parse_email(message.as_bytes())
    assert parsed.html_body is not None
    assert "Hello" in parsed.text_body


def test_inline_image_is_returned_as_inline_attachment():
    message = EmailMessage()
    message["From"] = "jane@example.org"
    message["Subject"] = "Screenshot"
    message["Message-ID"] = "<img1@example.org>"
    message.set_content("See attached")
    message.add_related(
        b"\x89PNG\r\n\x1a\n" + b"0" * 32,
        maintype="image",
        subtype="png",
        cid="<screenshot@local>",
        filename="screenshot.png",
    )

    parsed = parse_email(message.as_bytes())
    assert len(parsed.attachments) == 1
    attachment = parsed.attachments[0]
    assert attachment.is_inline is True
    assert attachment.content_id == "screenshot@local"
    assert attachment.filename == "screenshot.png"


def test_regular_attachment_is_not_inline():
    message = _build()
    message.add_attachment(
        b"%PDF-1.4 fake", maintype="application", subtype="pdf", filename="quote.pdf"
    )
    parsed = parse_email(message.as_bytes())

    assert len(parsed.attachments) == 1
    assert parsed.attachments[0].is_inline is False
    assert parsed.attachments[0].filename == "quote.pdf"


def test_encoded_subject_is_decoded():
    message = _build()
    del message["Subject"]
    message["Subject"] = "=?utf-8?q?Caf=C3=A9_machine_broken?="
    parsed = parse_email(message.as_bytes())
    assert parsed.subject == "Café machine broken"


def test_auto_reply_is_detected():
    message = _build(subject="Out of office: back Monday")
    message["Auto-Submitted"] = "auto-replied"
    parsed = parse_email(message.as_bytes())
    assert parsed.is_auto_reply is True


def test_normal_mail_is_not_flagged_as_auto_reply():
    assert parse_email(_build().as_bytes()).is_auto_reply is False


def test_references_are_collected_in_order():
    message = _build()
    message["References"] = "<first@example.org> <second@example.org>"
    message["In-Reply-To"] = "<second@example.org>"
    parsed = parse_email(message.as_bytes())

    assert parsed.references == ["<first@example.org>", "<second@example.org>"]
    assert parsed.in_reply_to == "<second@example.org>"


def test_plus_addressed_recipient_is_lowercased_and_kept():
    message = _build(to="HelpDesk+t.1042.ABCDEF0123456789@Test.Invalid")
    parsed = parse_email(message.as_bytes())
    assert parsed.to_addresses == ["helpdesk+t.1042.abcdef0123456789@test.invalid"]


class TestStripQuotedReply:
    def test_removes_on_wrote_block(self):
        body = (
            "Yes that fixed it, thanks!\n\n"
            "On Tue, 3 Feb 2026 at 09:12, Helpdesk <helpdesk@test.invalid> wrote:\n"
            "> Have you tried turning it off and on again?\n"
        )
        assert strip_quoted_reply(body) == "Yes that fixed it, thanks!"

    def test_removes_original_message_separator(self):
        body = "Still broken.\n\n-----Original Message-----\nFrom: someone\n"
        assert strip_quoted_reply(body) == "Still broken."

    def test_removes_reply_delimiter_block(self):
        body = "New info here.\n--- Please reply above this line ---\nold stuff"
        assert strip_quoted_reply(body) == "New info here."

    def test_keeps_whole_body_when_trimming_would_empty_it(self):
        body = "On Tue, someone wrote:\n> everything is quoted"
        assert strip_quoted_reply(body) == body.strip()

    def test_handles_empty_input(self):
        assert strip_quoted_reply("") == ""
