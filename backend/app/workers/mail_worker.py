"""Mail worker.

One loop, run in its own container:

  * fetch new IMAP messages and hand each to the inbound ingest pipeline;
  * flush the outbound queue over SMTP;
  * sweep expired sessions, rate-limit buckets and unclaimed uploads.

Everything is idempotent, so a crash mid-cycle costs at most a repeated poll.
"""

from __future__ import annotations

import logging
import signal
import ssl
import sys
import time
from types import FrameType

from app.config import settings
from app.database import session_scope
from app.security import ratelimit, sessions
from app.services import email_inbound, email_outbound
from app.services import tickets as ticket_service
from app.services.mail_tls import build_context

logging.basicConfig(
    level=getattr(logging, settings.log_level, logging.INFO),
    format="%(asctime)s %(levelname)-7s %(name)s %(message)s",
)
log = logging.getLogger("helpdesk.worker")

# How many messages to pull in one pass. Keeps a backlog from turning into one
# enormous transaction.
FETCH_BATCH = 25
MAINTENANCE_EVERY_CYCLES = 60

_shutdown = False


def _handle_signal(signum: int, _frame: FrameType | None) -> None:
    global _shutdown
    log.info("received signal %s, finishing current cycle", signum)
    _shutdown = True


def fetch_inbound() -> int:
    """Poll IMAP once. Returns the number of messages ingested."""
    if not settings.inbound_email_enabled or not settings.imap_host:
        return 0

    from imapclient import IMAPClient
    from imapclient.exceptions import IMAPClientError

    processed = 0
    use_implicit_tls = settings.imap_security == "ssl"
    context = (
        build_context(
            host=settings.imap_host,
            ca_cert=settings.imap_ca_cert,
            insecure=settings.imap_tls_insecure,
        )
        if settings.imap_security != "none"
        else None
    )
    try:
        with IMAPClient(
            host=settings.imap_host,
            port=settings.imap_port,
            ssl=use_implicit_tls,
            ssl_context=context if use_implicit_tls else None,
            timeout=60,
        ) as client:
            # Proton Mail Bridge and most local relays listen in the clear and
            # upgrade, rather than wrapping the socket from the start.
            if settings.imap_security == "starttls":
                client.starttls(ssl_context=context)
            client.login(settings.imap_username, settings.imap_password)
            client.select_folder(settings.imap_folder)

            uids = client.search(["UNSEEN"])[:FETCH_BATCH]
            if not uids:
                return 0
            log.info("found %s unread message(s)", len(uids))

            for uid in uids:
                fetched = client.fetch([uid], ["RFC822"])
                raw = fetched.get(uid, {}).get(b"RFC822")
                if not raw:
                    log.warning("uid %s returned no body, skipping", uid)
                    continue

                with session_scope() as db:
                    result = email_inbound.ingest(db, raw)
                log.info(
                    "uid %s -> %s (%s)", uid, result.outcome, result.ticket_key or result.detail
                )

                # Only move or flag once the transaction above committed, so a
                # crash leaves the mail unread and it is retried next cycle.
                if result.outcome in ("created", "appended", "duplicate", "ignored"):
                    _archive(client, uid)
                    processed += 1
    except (OSError, ssl.SSLError, IMAPClientError) as exc:
        log.error("IMAP poll failed: %s", exc)
    return processed


def _archive(client, uid: int) -> None:  # noqa: ANN001
    from imapclient.exceptions import IMAPClientError

    try:
        client.add_flags([uid], ["\\Seen"])
        if settings.imap_processed_folder:
            if not client.folder_exists(settings.imap_processed_folder):
                client.create_folder(settings.imap_processed_folder)
            client.move([uid], settings.imap_processed_folder)
    except IMAPClientError as exc:
        log.warning("could not archive uid %s: %s", uid, exc)


def flush_outbound() -> tuple[int, int]:
    with session_scope() as db:
        return email_outbound.flush_queue(db)


def maintenance() -> None:
    with session_scope() as db:
        expired_sessions = sessions.purge_expired(db)
        buckets = ratelimit.purge_expired(db)
        uploads = ticket_service.purge_unclaimed_uploads(db)
    if expired_sessions or buckets or uploads:
        log.info(
            "maintenance: %s session(s), %s rate-limit bucket(s), %s unclaimed upload(s) removed",
            expired_sessions,
            buckets,
            uploads,
        )


def main() -> int:
    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    log.info(
        "mail worker starting (inbound=%s outbound=%s interval=%ss)",
        settings.inbound_email_enabled,
        settings.outbound_email_configured,
        settings.imap_poll_seconds,
    )

    cycle = 0
    while not _shutdown:
        cycle += 1
        try:
            fetch_inbound()
            sent, failed = flush_outbound()
            if sent or failed:
                log.info("outbound: %s sent, %s failed", sent, failed)
            if cycle % MAINTENANCE_EVERY_CYCLES == 0:
                maintenance()
        except Exception:  # noqa: BLE001 - the loop must survive anything
            log.exception("worker cycle failed")

        # Sleep in short slices so SIGTERM is honoured quickly.
        for _ in range(max(1, settings.imap_poll_seconds)):
            if _shutdown:
                break
            time.sleep(1)

    log.info("mail worker stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
