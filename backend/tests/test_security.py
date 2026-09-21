"""Password policy, TOTP, reply-address signing, sanitising and rate limits."""

from __future__ import annotations

import time

import pyotp
import pytest
from sqlalchemy.orm import Session

from app.security import mail_tokens, passwords, ratelimit, totp
from app.security.crypto import decrypt_secret, encrypt_secret
from app.services import sanitize, storage


class TestPasswords:
    def test_hash_and_verify_round_trip(self):
        digest = passwords.hash_password("correct horse battery staple")
        assert passwords.verify_password(digest, "correct horse battery staple") is True
        assert passwords.verify_password(digest, "wrong password entirely") is False

    def test_hashes_are_salted(self):
        assert passwords.hash_password("same-password-1") != passwords.hash_password(
            "same-password-1"
        )

    def test_missing_hash_never_verifies(self):
        assert passwords.verify_password(None, "anything") is False
        assert passwords.verify_password("", "anything") is False

    def test_short_password_is_rejected(self):
        assert passwords.password_problems("Short1!") != []

    def test_long_passphrase_is_accepted_without_symbols(self):
        assert passwords.password_problems("correct horse battery staple ok") == []

    def test_password_containing_the_email_is_rejected(self):
        problems = passwords.password_problems("jsmith-Password1!", email="jsmith@example.org")
        assert any("email" in p for p in problems)


class TestTotp:
    def test_valid_code_is_accepted(self):
        secret = totp.generate_secret()
        code = pyotp.TOTP(secret).now()
        assert totp.verify_code(secret, code) is not None

    def test_wrong_code_is_rejected(self):
        secret = totp.generate_secret()
        assert totp.verify_code(secret, "000000") is None

    def test_malformed_code_is_rejected(self):
        secret = totp.generate_secret()
        for bad in ("", "abcdef", "12345", "1234567890"):
            assert totp.verify_code(secret, bad) is None

    def test_code_cannot_be_replayed(self):
        secret = totp.generate_secret()
        code = pyotp.TOTP(secret).now()
        counter = totp.verify_code(secret, code)
        assert counter is not None
        # Same code, now that the counter has been recorded.
        assert totp.verify_code(secret, code, last_counter=counter) is None

    def test_drift_of_one_step_is_tolerated(self):
        secret = totp.generate_secret()
        previous = pyotp.TOTP(secret).at(time.time() - totp.PERIOD)
        assert totp.verify_code(secret, previous) is not None

    def test_recovery_codes_are_unique_and_hashable(self):
        codes = totp.generate_recovery_codes()
        assert len(codes) == len(set(codes)) == totp.RECOVERY_CODE_COUNT
        assert totp.hash_recovery_code(codes[0]) == totp.hash_recovery_code(
            codes[0].upper().strip()
        )

    def test_provisioning_uri_and_qr(self):
        secret = totp.generate_secret()
        uri = totp.provisioning_uri(secret, "user@example.org")
        assert uri.startswith("otpauth://totp/")
        assert totp.qr_svg(uri).lstrip().startswith("<?xml") or "<svg" in totp.qr_svg(uri)


class TestSecretEncryption:
    def test_round_trip(self):
        assert decrypt_secret(encrypt_secret("JBSWY3DPEHPK3PXP")) == "JBSWY3DPEHPK3PXP"

    def test_ciphertext_is_not_the_plaintext(self):
        assert "JBSWY3DPEHPK3PXP" not in encrypt_secret("JBSWY3DPEHPK3PXP")

    def test_tampered_ciphertext_is_rejected(self):
        blob = encrypt_secret("JBSWY3DPEHPK3PXP")
        with pytest.raises(ValueError):
            decrypt_secret(blob[:-4] + "AAAA")


class TestMailTokens:
    def test_signature_verifies(self):
        signature = mail_tokens.sign(1042, "token-abc")
        assert mail_tokens.verify(1042, "token-abc", signature) is True

    def test_signature_is_bound_to_the_ticket_number(self):
        signature = mail_tokens.sign(1042, "token-abc")
        assert mail_tokens.verify(1043, "token-abc", signature) is False

    def test_signature_is_bound_to_the_ticket_token(self):
        signature = mail_tokens.sign(1042, "token-abc")
        assert mail_tokens.verify(1042, "token-xyz", signature) is False

    def test_reply_address_round_trips(self):
        address = mail_tokens.reply_address("helpdesk@example.org", 77, "seed")
        parsed = mail_tokens.parse_recipients([address])
        assert parsed is not None
        number, signature = parsed
        assert number == 77
        assert mail_tokens.verify(number, "seed", signature) is True

    def test_subject_tag_is_added_once(self):
        tagged = mail_tokens.tag_subject("Printer broken", 12)
        assert tagged == "[TKT-12] Printer broken"
        assert mail_tokens.tag_subject(tagged, 12) == tagged

    def test_subject_tag_is_parsed(self):
        assert mail_tokens.parse_subject("Re: [TKT-12] Printer broken") == 12
        assert mail_tokens.parse_subject("no tag here") is None


class TestSanitiser:
    def test_script_tags_are_removed(self):
        assert "<script" not in sanitize.clean_html("<p>hi</p><script>alert(1)</script>")

    def test_event_handlers_are_removed(self):
        cleaned = sanitize.clean_html('<img src="cid:x" onerror="alert(1)">')
        assert "onerror" not in cleaned

    def test_javascript_urls_are_removed(self):
        cleaned = sanitize.clean_html('<a href="javascript:alert(1)">click</a>')
        assert "javascript:" not in cleaned

    def test_iframes_are_removed(self):
        assert "<iframe" not in sanitize.clean_html('<iframe src="https://evil.invalid"></iframe>')

    def test_style_blocks_are_removed(self):
        assert "body{" not in sanitize.clean_html("<style>body{display:none}</style><p>x</p>")

    def test_formatting_survives(self):
        cleaned = sanitize.clean_html("<p>Hello <strong>there</strong></p>")
        assert "<strong>there</strong>" in cleaned

    def test_cid_images_are_rewritten(self):
        html, blocked = sanitize.prepare_email_html(
            '<img src="cid:shot@local">', {"shot@local": "/api/attachments/abc/inline"}
        )
        assert "/api/attachments/abc/inline" in html
        assert blocked is False

    def test_unknown_cid_is_dropped(self):
        html, _ = sanitize.prepare_email_html('<img src="cid:missing">', {})
        assert "cid:missing" not in html

    def test_remote_images_are_stripped_and_reported(self):
        html, blocked = sanitize.prepare_email_html(
            '<p>hi</p><img src="https://tracker.invalid/p.gif">', {}
        )
        assert "tracker.invalid" not in html
        assert blocked is True

    def test_text_to_html_escapes_markup(self):
        assert "<script>" not in sanitize.text_to_html("<script>alert(1)</script>")


class TestStorage:
    def test_path_traversal_in_a_filename_is_flattened(self):
        assert storage.safe_filename("../../etc/passwd") == "etc_passwd" or "/" not in (
            storage.safe_filename("../../etc/passwd")
        )

    def test_null_bytes_are_removed(self):
        assert "\x00" not in storage.safe_filename("evil\x00.png")

    def test_blank_filename_falls_back(self):
        assert storage.safe_filename("") == "attachment"

    def test_declared_image_type_is_overridden_when_bytes_disagree(self):
        assert storage.sniff_content_type(b"<html>nope", "image/png") == "application/octet-stream"

    def test_real_png_is_detected(self):
        assert storage.sniff_content_type(b"\x89PNG\r\n\x1a\n...", "text/plain") == "image/png"

    def test_resolve_refuses_to_escape_the_root(self):
        with pytest.raises(ValueError):
            storage.resolve("../../etc/passwd")

    def test_identical_bytes_are_stored_once(self):
        first = storage.store_bytes(b"same bytes", suffix=".txt")
        second = storage.store_bytes(b"same bytes", suffix=".txt")
        assert first[0] == second[0]
        assert first[1] == second[1]

    def test_oversized_upload_is_refused(self, monkeypatch):
        monkeypatch.setattr("app.services.storage.settings.max_attachment_mb", 0)
        with pytest.raises(storage.AttachmentTooLarge):
            storage.store_bytes(b"x" * 10)


class TestRateLimit:
    def test_allows_up_to_the_limit_then_blocks(self, db: Session):
        for _ in range(3):
            assert ratelimit.hit(db, "test:key", limit=3, window_seconds=60).allowed is True
        blocked = ratelimit.hit(db, "test:key", limit=3, window_seconds=60, block_seconds=30)
        assert blocked.allowed is False
        assert blocked.retry_after_seconds > 0

    def test_reset_clears_the_bucket(self, db: Session):
        for _ in range(4):
            ratelimit.hit(db, "test:reset", limit=3, window_seconds=60)
        ratelimit.reset(db, "test:reset")
        assert ratelimit.hit(db, "test:reset", limit=3, window_seconds=60).allowed is True

    def test_buckets_are_independent(self, db: Session):
        for _ in range(4):
            ratelimit.hit(db, "test:a", limit=3, window_seconds=60)
        assert ratelimit.hit(db, "test:b", limit=3, window_seconds=60).allowed is True
