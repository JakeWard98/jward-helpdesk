# CLAUDE.md

Guidance for Claude Code working in this repository.

## What this is

A self-hosted helpdesk with a web GUI and a two-way email loop, deployed as a
Portainer stack behind a Cloudflare tunnel. **The repository is public** — no
secrets, no real hostnames, no customer data in commits, tests or fixtures.
Use `example.com` / `example.org` in anything committed.

## Stack

| Layer | Choice |
| --- | --- |
| API | Python 3.12, FastAPI, SQLAlchemy 2.0 (sync), Pydantic v2 |
| Database | PostgreSQL 16 |
| Frontend | React 18 + TypeScript + Vite, no UI framework, plain CSS |
| Mail | `imapclient` in, `smtplib` out, both only in the worker container. Proton SMTP submission for sending; Proton Bridge for receiving |
| Deploy | Docker Compose, four services: `db`, `api`, `worker`, `web`. Nginx Proxy Manager fronts it, Cloudflare tunnel fronts NPM |

## Ground rules

**Security is the point of this project.** It is exposed to the internet
through a tunnel. When a change touches auth, sessions, uploads, mail parsing
or HTML rendering, treat the security property as the requirement, not a
nice-to-have.

- **Never** log or store a password, TOTP code, session token or recovery code.
  `services/audit.py` scrubs keys containing `password`, `code`, `token`,
  `secret` or `totp` — do not route around it.
- **Never** render email HTML that has not been through
  `services/sanitize.py`. `tickets.add_message` re-sanitises on purpose; it is
  the single choke point before the GUI.
- **Never** trust a filename, a declared Content-Type, or an *unsigned*
  subject tag. Filenames go through `storage.safe_filename`, types through
  `storage.sniff_content_type`, and a bare `[TKT-n]` only routes mail from
  someone already on the ticket.
- **Never** put the ticket reference in the email address. Providers mangle
  plus-addressed local parts; the signed reference lives in the subject tag and
  the body footer, and both are verified against the ticket's `reply_token`.
- **Never** disable mail TLS verification for a non-local host.
  `mail_tls.build_context` enforces that, and config validation catches it at
  boot.
- **Never** let the API container reach the network. Outbound mail is queued in
  `outbound_emails` and sent by the worker. Compose enforces this; keep it that
  way.
- Secrets come from the environment only. No defaults that work in production.

## Conventions

- **Async**: the API is sync SQLAlchemy on purpose (IMAP and SMTP libraries are
  blocking). Do not introduce a mixed async/sync session layer.
- **Timestamps**: always timezone-aware UTC. Use `app.utils.now_utc()` and pass
  anything read from the database through `app.utils.as_utc()` before comparing
  it — drivers differ on whether they hand back aware values.
- **Enums** are plain strings with a `CheckConstraint`, so adding a value needs
  no migration. The tuples live at the top of `models.py`.
- **Schema changes**: `init_db()` uses `create_all`, which only adds. The
  moment a column has to *change*, add Alembic rather than editing a live
  table by hand.
- **JSON columns** use `models.JSONType` (JSONB on Postgres, JSON elsewhere) so
  the test suite can run on SQLite.
- **Errors**: never return a stack trace or driver message to a client. The
  handler in `main.py` logs the detail and returns a request id.
- **Ticket lookups** return 404 for "not yours" as well as "not found", so the
  API cannot be used to probe which ticket ids exist.

## Commands

```bash
cd backend && pytest                 # full suite, no containers required
cd backend && pytest -k email        # threading and parsing only
cd backend && ruff check . --fix
cd frontend && npm run build         # tsc + vite, type errors fail the build
docker compose --env-file .env config -q   # validate the stack
```

Tests run against SQLite for speed. Anything Postgres-specific (the
`ticket_number_seq` sequence, JSONB) has a documented fallback — if you add
something else Postgres-only, add the fallback too or the suite stops running
locally.

## Where things live

| Task | File |
| --- | --- |
| Sign-in, MFA, password changes | `backend/app/api/auth.py` |
| Who can see what | `backend/app/deps.py`, `services/tickets.py::can_view` |
| Inbound mail routing | `backend/app/services/email_inbound.py` |
| Mail parsing (pure functions) | `backend/app/services/email_parse.py` |
| Outbound queue and SMTP | `backend/app/services/email_outbound.py` |
| Subject / body reference signing | `backend/app/security/mail_tokens.py` |
| Mail TLS, Proton Bridge certs | `backend/app/services/mail_tls.py` |
| HTML sanitising | `backend/app/services/sanitize.py` |
| Attachment storage | `backend/app/services/storage.py` |
| Security headers, body limits | `backend/app/middleware.py` |
| Default templates | `backend/app/services/templates.py` |

## Testing expectations

A change to routing, permissions or sanitising needs a test that would fail
without it. The existing suite already covers the adversarial cases — forged
reply signatures, subject tags from strangers, path traversal in filenames,
replayed TOTP codes, missing CSRF headers. Follow that pattern rather than only
testing the happy path.

## Style

- Comments explain *why*, and are worth writing where a security property or a
  non-obvious ordering constraint is at stake. Skip them where the code says it.
- British spelling in prose and comments (`sanitise`, `enrolment`), except where
  it is an identifier that already exists.
- Frontend: no state library, no component library. `useState` and a context
  for auth are enough at this size.
