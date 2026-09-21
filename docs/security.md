# Security notes

[SECURITY.md](../SECURITY.md) is the policy and the summary table. This file is
the *why* behind the choices, for when you come back to the code in a year.

## Authentication

**Passwords** are Argon2id at 64 MiB / 3 passes / 2 lanes. That is deliberately
slower than the defaults some libraries ship — a homelab box can afford ~100ms
per sign-in, and it is the single cheapest thing that makes an offline crack of
a stolen database expensive.

An account with no password hash (created automatically when someone emails in)
still runs a full hash computation on a login attempt. Otherwise the response
time would reveal which addresses are email-only accounts.

Unknown account and wrong password return exactly the same message and take
roughly the same time, so the sign-in page is not a user enumerator.

**MFA** is TOTP, 30-second step, six digits, one step of drift tolerated either
side. The matched counter is stored on the user, so a code cannot be used twice
even inside its window — that closes the replay gap a shoulder-surfer or a
proxy-in-the-middle would otherwise have.

TOTP seeds have to be reversible to verify a code, so they are encrypted with
Fernet under a key derived from `APP_SECRET` with an HKDF-style expansion. The
mail-token key comes from the same secret but a different info string, so a
compromise of one does not weaken the other.

Recovery codes are hashed like passwords would be — they are credentials.

## Sessions

The cookie carries a 256-bit random token and nothing else. Everything of
substance lives in the `auth_sessions` row, which means a session can be
revoked instantly, and a stolen cookie value tells an attacker nothing about
who it belongs to.

The `__Host-` prefix is used whenever cookies are Secure. Browsers only accept
that prefix when the cookie is Secure, `Path=/` and carries no `Domain`
attribute, which rules out a sibling subdomain of your tunnel hostname setting
a session cookie for the helpdesk.

`mfa_satisfied` starts false and only flips after the second factor. A session
in that state can reach the MFA endpoints and nothing else, so a correct
password on its own never touches ticket data.

The token is rotated after MFA and after a password change. That is the
standard defence against session fixation: a token captured before the
privilege change is useless afterwards.

## CSRF

Cookie authentication means the browser attaches credentials to cross-site
requests, so every state-changing call needs proof it came from our own app.

The token lives in the session row and is also set as a non-`HttpOnly` cookie.
The SPA reads it and echoes it in `X-CSRF-Token`; the server compares it in
constant time against the row. Another origin's JavaScript cannot read our
cookie, so it cannot produce the header. `SameSite=Strict` on the session
cookie is a second layer, not the only one.

## Inbound email

This is the most interesting attack surface, because anyone can send email.

The **signed reply address** is the primary routing signal:
`helpdesk+t.1042.<hmac>@example.com`, where the HMAC covers the ticket number
and a random per-ticket token. Two properties matter: it cannot be forged
without `APP_SECRET`, and seeing one ticket's address gives you nothing towards
another's, because the per-ticket token is independent random data.

**Threading headers** are trusted as a secondary signal, because matching a
`Message-ID` we generated ourselves is a reasonable proof of having received our
mail.

**Subject tags** are the weak signal, and are treated as such: `[TKT-1042]` is
accepted only from the requester, from staff, or from an address that has
already written on that ticket. Without that check, anyone who could guess a
ticket number could inject a message into somebody else's ticket — and ticket
numbers are sequential, so guessing is trivial.

De-duplication is by `Message-ID`, recorded before the transaction commits. The
worker only marks a message read (or moves it) *after* the transaction
succeeds, so a crash mid-ingest leaves the mail unread and it is retried, rather
than being lost.

## Rendering email HTML

Mail bodies are hostile input rendered into an authenticated page, which is the
classic stored-XSS shape. Three layers:

1. **nh3 (Ammonia)** strips everything but a small allowlist of formatting tags
   and attributes — no script, style, iframe, form, object, or event handlers,
   and only `http`, `https`, `mailto` and `cid` URL schemes.
2. **`cid:` rewriting and remote-image stripping**: inline images become
   same-origin `/api/attachments/…` URLs; anything loading from another host is
   removed entirely. This is a privacy control as much as a security one — it
   stops tracking pixels and prevents the reader's IP reaching the sender.
3. **CSP**: the SPA is served with `script-src 'self'`, so even if something
   slipped through the sanitiser it could not execute inline.

`tickets.add_message` re-sanitises even when the caller already did. It is the
single choke point before the GUI, and being idempotent costs nothing.

## Attachments

Storage paths are generated from a SHA-256 of the content, never from the
supplied filename, so a name like `../../etc/cron.d/x` has nowhere to go. The
original name is kept only for display and for the download header, flattened
to a safe character set.

Content types are sniffed from magic bytes. A file claiming `image/png` that is
not one is downgraded to `application/octet-stream`, which means it cannot be
served inline. SVG is deliberately not in the inline-safe set: it can carry
script and would execute on our origin.

Everything downloads as `Content-Disposition: attachment` with `nosniff` and a
`default-src 'none'; sandbox` CSP unless it is a verified raster image.

Uploads are staged before a ticket exists, so an upload id could otherwise be a
way to pull in someone else's file. Claiming an attachment requires it to be
unclaimed *and* owned by the same uploader. Unclaimed uploads are swept after
24 hours.

## Network shape

The `api` container is on an `internal: true` network only, so it cannot make
outbound connections at all. Every piece of mail I/O belongs to `worker`, which
is the only application container with egress. If the API is ever compromised
through a request-handling bug, there is no route out for data.

`web` is on two internal networks; the only container that can reach the
internet from the `edge` side is `cloudflared`.

## Things deliberately not done

- **No JWTs in local storage.** Server-side sessions are revocable and are not
  readable by injected script.
- **No password reset by email.** A self-service reset flow on an
  internet-facing helpdesk is a large surface for a small homelab. An admin
  resets a password, and the account must change it at next sign-in.
- **No API docs in production.** `/api/docs` and the OpenAPI schema are only
  mounted when `APP_ENV=dev`.
- **No stack traces in responses.** The error handler logs the detail with a
  request id and returns only that id.
