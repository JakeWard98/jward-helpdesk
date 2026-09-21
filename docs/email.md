# Email

## Mailbox setup

One mailbox does both directions. It needs:

- **IMAP** access, so the worker can read new mail;
- **SMTP** access, so the worker can send replies;
- **plus-addressing** (`helpdesk+anything@example.com` delivered to
  `helpdesk@example.com`). Gmail, Fastmail, Proton (with a custom domain),
  Zoho and most self-hosted servers do this. Some providers use `-` or `_`
  instead of `+`, and a few do not support it at all — see
  [without plus-addressing](#without-plus-addressing).

Use an app password, not the account password, wherever the provider offers one.

```
SMTP_HOST=smtp.example.com
SMTP_PORT=587
SMTP_SECURITY=starttls
SMTP_USERNAME=helpdesk@example.com
SMTP_PASSWORD=<app password>
SMTP_FROM_EMAIL=helpdesk@example.com
SMTP_REPLY_TO_EMAIL=helpdesk@example.com

INBOUND_EMAIL_ENABLED=true
IMAP_HOST=imap.example.com
IMAP_PORT=993
IMAP_USERNAME=helpdesk@example.com
IMAP_PASSWORD=<app password>
IMAP_FOLDER=INBOX
IMAP_PROCESSED_FOLDER=Processed
IMAP_POLL_SECONDS=60
```

`IMAP_PROCESSED_FOLDER` is optional. Set it and ingested mail is moved there,
which keeps the inbox clean and makes it obvious what has been handled. Leave
it empty and messages are only marked read.

TLS certificates are always verified. There is no option to turn that off.

## The round trip

```
person emails helpdesk@example.com
        │
        ▼
worker polls IMAP (every IMAP_POLL_SECONDS)
        │
        ▼
ticket TKT-1042 created on the fallback template
        │
        ▼
acknowledgement sent, with
  Reply-To: helpdesk+t.1042.9f3c1ab27d0e4a56@example.com
  Subject:  [TKT-1042] …
        │
        ▼
agent replies in the GUI  ──▶  queued in outbound_emails  ──▶  worker sends it
        │
        ▼
person hits reply in their mail client
        │
        ▼
worker matches the signed address, appends to TKT-1042
        │
        ▼
agent sees it in the timeline; the requester sees it in their portal too
```

## How a reply is matched

In order, first match wins:

1. **Signed reply address.** `helpdesk+t.<number>.<hmac>@example.com`, where
   the HMAC covers the ticket number and a random 16-byte token stored on the
   ticket, keyed by `APP_SECRET`. Forging one for a ticket you cannot see is
   not feasible, and knowing one tells you nothing about another ticket's.
2. **Threading headers.** `In-Reply-To` and `References` are matched against
   `Message-ID`s the helpdesk sent or previously ingested. This catches clients
   that reply to `From` rather than `Reply-To`.
3. **Subject tag.** `[TKT-1042]` — accepted **only** when the sender is already
   the requester, a member of staff, or someone who has previously written on
   that ticket. Anyone can copy a subject line, so on its own it must not grant
   access.

No match means a new ticket on the fallback template.

Every message is recorded in `processed_inbound_emails` by `Message-ID`, so
re-reading a folder never duplicates a ticket. Mail that arrives without a
`Message-ID` gets a synthetic one derived from a hash of the raw bytes, which
de-duplicates it just as well.

## Loop protection

Three things stop a mail loop:

- Mail from the helpdesk's own address is ignored outright.
- An auto-reply (`Auto-Submitted`, `X-Autoreply`, `Precedence: bulk`, or an
  "Out of office" subject) that matches no ticket is dropped instead of
  creating one. If it *does* match a ticket it is logged on the timeline, and
  no acknowledgement goes back.
- Outbound mail carries `Auto-Submitted: auto-replied` and
  `X-Auto-Response-Suppress`, which tells well-behaved responders not to answer.

## Attachments

Everything attached to an inbound message is stored and appears on the ticket:

- **Inline images** (`Content-ID`, referenced as `cid:` in the HTML body) are
  rewritten to `/api/attachments/<id>/inline` and render in the message body.
- **Regular attachments** are listed under the message and download on click.
- **Remote images** (`<img src="https://…">`) are removed and the message is
  marked "Images hosted elsewhere were removed from this email." That kills
  tracking pixels and stops your IP leaking to whoever sent it.

Anything larger than `MAX_ATTACHMENT_MB` (default 25) is skipped with a warning
in the worker log; the message itself is still ingested. A whole email larger
than `MAX_EMAIL_MB` (default 40) is dropped.

Attachments an agent adds to a reply are sent with the outgoing email.

## Outbound queue

The API never talks to SMTP. It writes to `outbound_emails` in the same
transaction as the ticket message, and the worker sends it. That means:

- a slow mail server never blocks the GUI;
- a restart mid-send loses nothing;
- the API container needs no network egress at all.

Failed sends retry with quadratic backoff (1, 4, 9, 16, 25 minutes) up to six
attempts, then stop and show under **Admin → Mail** as failed.

## Troubleshooting

### Nothing is being ingested

```bash
docker logs -f helpdesk-worker
```

Look for `IMAP poll failed`. Common causes: `INBOUND_EMAIL_ENABLED` still
`false`, an app password that was never generated, or the provider requiring
OAuth rather than a password.

Note the worker only reads **unread** mail. If you have been testing by reading
messages in a mail client first, they will be skipped.

### Replies create new tickets

Check the `Reply-To` on an email the helpdesk actually sent:

- If it has no `+t.` part, `SMTP_REPLY_TO_EMAIL` is unset or has no local part.
- If it is there but replies still miss, the provider is probably dropping the
  plus-address. Send a test to `helpdesk+test@example.com` and see whether it
  arrives.
- If `APP_SECRET` was rotated, every previously sent reply address stops
  verifying — threading headers and the subject tag still catch most replies,
  but old signed addresses are dead.

### Emails queue but never send

`docker logs helpdesk-worker` shows the SMTP error. Check the `worker`
container is attached to the `egress` network (`docker inspect helpdesk-worker`)
— it is the only service besides the tunnel that is allowed out.

### Without plus-addressing

If your provider cannot do it, threading headers (signal 2) still handle most
replies, and the `[TKT-n]` subject tag handles the rest for people already on
the ticket. Set `SMTP_REPLY_TO_EMAIL` to the plain address. The practical loss
is that a reply from a client which strips both the headers and the subject tag
will start a new ticket.

A cleaner alternative is a catch-all mailbox: point `helpdesk-*@example.com` or
a dedicated subdomain at the same mailbox, and plus-addressing works again.
