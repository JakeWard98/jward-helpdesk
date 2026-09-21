# Security policy

## Reporting a vulnerability

This is a personal homelab project, but the code is public and other people may
run it. If you find a vulnerability:

- **Do not open a public issue.**
- Use GitHub's private reporting: **Security → Report a vulnerability** on
  <https://github.com/JakeWard98/jward-helpdesk>.
- Include what you did, what happened, and what you expected. A proof of
  concept helps.

There is no bounty and no SLA. Expect a reply when the maintainer next has an
evening free.

## Supported versions

The `main` branch only. There are no back-ported fixes.

## What this project defends against

The helpdesk is designed to be reachable from the internet through a Cloudflare
tunnel, so the threat model assumes anyone can reach the sign-in page and
anyone can send it email.

| Area | Control |
| --- | --- |
| Passwords | Argon2id (64 MiB, 3 passes), per-user salt, length and complexity policy, rehash on parameter change |
| Multi-factor | TOTP (RFC 6238), seeds encrypted at rest with a key derived from `APP_SECRET`, used codes rejected on replay, single-use recovery codes stored as hashes |
| Sessions | Opaque 256-bit tokens, stored server-side as hashes, `__Host-` prefixed `HttpOnly` `SameSite=Strict` cookies, idle and absolute timeouts, rotation on privilege change, revocable individually or all at once |
| Brute force | Per-IP and per-account fixed-window limits shared across workers, account lockout, identical error message and timing for unknown and wrong-password accounts |
| CSRF | Synchroniser token held in the session row, echoed in `X-CSRF-Token`, required on every state-changing request |
| Authorisation | Role checks in dependencies; requesters are scoped to their own tickets and never see internal notes; "not yours" returns 404, not 403 |
| Email injection | Reply addresses signed with HMAC over ticket number and a per-ticket token; subject tags accepted only from existing participants; `Message-ID` deduplication; auto-reply loop detection |
| Mail HTML | Sanitised with nh3 (script, style, event handlers, frames and forms removed), remote images stripped, `cid:` images rewritten to same-origin URLs |
| Uploads | Generated storage paths, flattened filenames, magic-byte content sniffing, size caps, everything served `nosniff` and `Content-Disposition: attachment` unless it is a verified image |
| Transport | HSTS, strict CSP, `frame-ancestors 'none'`, `no-referrer`, no API response cached |
| Network | API container has no egress; database is on an `internal` network; nothing is published to a host port |
| Auditing | Append-only audit log of every authentication event and agent action, with secrets scrubbed |

## What it does not defend against

Worth being honest about:

- **A compromised Cloudflare tunnel or account.** The tunnel is the perimeter.
- **A malicious admin.** Admins can reset MFA and passwords for other accounts.
  That is the intended recovery path; it is logged, not prevented.
- **Mail spoofing upstream.** Anything the mailbox accepts, the helpdesk
  ingests. Configure SPF, DKIM and DMARC on the mail provider — the signed
  reply address protects ticket routing, not sender identity.
- **Denial of service.** Rate limits protect credentials, not capacity.
- **Data at rest.** The database volume is not encrypted; use an encrypted
  filesystem if that matters to you.

## Operational expectations

If you run this:

- Generate `APP_SECRET` and the database password yourself, 32 random bytes each.
- Clear `BOOTSTRAP_ADMIN_PASSWORD` from the stack environment after first login.
- Leave `REQUIRE_MFA_FOR_AGENTS=true`.
- Only set `TRUST_CLOUDFLARE_HEADERS=true` when the tunnel is genuinely the
  only route to the container. Otherwise the header is attacker-controlled and
  the rate limiter can be bypassed.
- Consider putting Cloudflare Access in front of the whole thing, so
  unauthenticated traffic never reaches the app at all.
- Keep backups of the `db-data` and `attachments` volumes, and test restoring
  them.
