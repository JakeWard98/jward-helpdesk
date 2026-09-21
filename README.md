# jward-helpdesk

A self-hosted helpdesk for a homelab: a web GUI for raising and working tickets,
plus a real email loop so people can just reply to the mail they were sent and
have it land on the right ticket.

Runs as a Portainer stack behind a Cloudflare tunnel. Nothing is published on a
host port.

```
                   ┌──────────────┐
  browser  ───────▶│ cloudflared  │ (edge network, no LAN port)
                   └──────┬───────┘
                          ▼
                   ┌──────────────┐        ┌──────────────┐
                   │ web (nginx)  │───────▶│ api (FastAPI)│
                   │ SPA + /api   │        └──────┬───────┘
                   └──────────────┘               │
                                                  ▼
                   ┌──────────────┐        ┌──────────────┐
  mail server ◀───▶│ worker       │───────▶│ db (Postgres)│
  (IMAP/SMTP)      │ mail in/out  │        └──────────────┘
                   └──────────────┘
```

## What it does

**Sign-in**
- Email and password (Argon2id), with a length and complexity policy
- TOTP multi-factor, mandatory for agents and admins by default
- Ten single-use recovery codes, shown once at enrolment
- Server-side sessions with idle and absolute timeouts, revocable from the GUI

**Tickets**
- Pick a template when raising a ticket; each template defines its own fields
- Emailed-in tickets get the fallback template, and an agent re-templates them
  in the GUI afterwards
- Statuses, priorities, assignment, internal notes, full audit trail
- Requesters see only their own tickets, and never internal notes

**Email**
- Replies you write in the GUI are emailed to the requester
- Their answer is logged against the same ticket instead of creating a new one,
  and shows in their portal view too
- Images and files attached to an email become attachments in the GUI; inline
  images render in the message body
- Remote images are stripped, so tracking pixels do not fire

## Quick start

### Portainer (how this is meant to run)

1. **Stacks → Add stack → Repository**, pointed at this repo, compose path
   `docker-compose.yml`.
2. Fill the **Environment variables** pane from [`.env.example`](.env.example).
   Generate every secret yourself:

   ```fish
   # CachyOS / fish
   for name in APP_SECRET POSTGRES_PASSWORD
       echo "$name="(openssl rand -hex 32)
   end
   ```

   ```bash
   # Fedora / bash
   for name in APP_SECRET POSTGRES_PASSWORD; do
     echo "$name=$(openssl rand -hex 32)"
   done
   ```

3. Deploy. On first boot the API creates the schema, seeds the default
   templates, and creates the admin from `BOOTSTRAP_ADMIN_EMAIL` /
   `BOOTSTRAP_ADMIN_PASSWORD`.
4. Point your Cloudflare tunnel's public hostname at `http://helpdesk-web:8080`
   (see [docs/cloudflare-tunnel.md](docs/cloudflare-tunnel.md)).
5. Sign in. You are made to change the password and enrol MFA immediately.
6. **Clear `BOOTSTRAP_ADMIN_PASSWORD` from the stack environment and redeploy.**
   It is only read when no admin exists, but there is no reason to leave a
   password sitting in the stack definition.

### Local development

```bash
# Fedora / bash - backend
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
cp ../.env.example ../.env   # set APP_ENV=dev, SESSION_COOKIE_SECURE=false
uvicorn app.main:app --reload

# frontend, in a second terminal
cd frontend && npm install && npm run dev   # http://localhost:5173
```

```fish
# CachyOS / fish - backend
cd backend
python3 -m venv .venv; and source .venv/bin/activate.fish
pip install -r requirements-dev.txt
uvicorn app.main:app --reload
```

`APP_ENV=dev` relaxes the production guard rails (it allows `http://`
and a non-Secure cookie) and mounts the API docs at `/api/docs`. Never run a
tunnelled deployment with it.

## Configuration

Everything comes from the environment; there is no config file to edit.
[`.env.example`](.env.example) is the full list, with comments. The ones worth
understanding:

| Variable | Why it matters |
| --- | --- |
| `APP_SECRET` | Signs sessions, CSRF tokens and mail reply tokens, and encrypts TOTP seeds. Rotating it signs everyone out and breaks stored TOTP secrets. |
| `PUBLIC_BASE_URL` | Must be the `https://` origin people actually use. In `prod` the app refuses to start on `http://`. |
| `SMTP_REPLY_TO_EMAIL` | Replies come back here as `helpdesk+t.1042.<signature>@…`. Your provider must accept plus-addressing. |
| `REQUIRE_MFA_FOR_AGENTS` | Forces MFA enrolment for agents and admins at first sign-in. Leave it on. |
| `TRUST_CLOUDFLARE_HEADERS` | Trusts `CF-Connecting-IP` for rate limiting. Only correct when the tunnel is the *only* route in. |

In `prod` the app refuses to start on a placeholder `APP_SECRET`, a non-HTTPS
`PUBLIC_BASE_URL`, or `SESSION_COOKIE_SECURE=false`.

## How email threading works

An inbound message is matched to a ticket by the first of these that hits:

1. **Signed reply address** — `helpdesk+t.1042.<hmac>@example.com`. The HMAC
   covers the ticket number and a random per-ticket token, so it cannot be
   forged or guessed from another ticket's address.
2. **Threading headers** — `In-Reply-To` / `References` matched against the
   `Message-ID`s we sent or previously ingested.
3. **Subject tag** — `[TKT-1042]`, accepted *only* when the sender is already
   the requester, an agent, or an existing participant. A subject line is
   trivially copied, so on its own it never grants access to a ticket.

Anything unmatched becomes a new ticket on the fallback template. Messages are
recorded by `Message-ID`, so re-reading a mailbox cannot duplicate a ticket, and
unmatched auto-replies are dropped rather than starting a mail loop.

Full detail, including what to do when replies land as new tickets, is in
[docs/email.md](docs/email.md).

## Repository layout

```
backend/           FastAPI application
  app/api/         HTTP routes
  app/services/    tickets, templates, mail in/out, sanitising, storage
  app/security/    passwords, TOTP, sessions, CSRF, rate limits, mail tokens
  app/workers/     IMAP poller and outbound sender
  tests/           111 tests, no containers needed
frontend/          React + TypeScript SPA, served by nginx
docs/              deployment, email, security hardening, Cloudflare tunnel
docker-compose.yml Portainer stack
```

## Testing

```bash
cd backend && pytest                 # 111 tests, SQLite-backed
cd backend && ruff check .
cd frontend && npm run build         # type-checks as part of the build
```

The suite covers email parsing and threading, the routing rules above
(including the forgery and stranger cases), sanitising, storage, password and
TOTP handling, rate limiting, CSRF, and the ticket/permission API.

## Documentation

- [docs/deployment.md](docs/deployment.md) — Portainer stack, upgrades, backups
- [docs/email.md](docs/email.md) — mailbox setup, threading, troubleshooting
- [docs/security.md](docs/security.md) — what is defended and how
- [docs/cloudflare-tunnel.md](docs/cloudflare-tunnel.md) — tunnel and Access
- [SECURITY.md](SECURITY.md) — reporting a vulnerability
- [CLAUDE.md](CLAUDE.md) — conventions for working in this repo

## Licence

[MIT](LICENSE)
