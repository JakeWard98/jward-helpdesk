# Deployment

## Portainer stack

1. **Stacks → Add stack → Repository**
   - Repository URL: `https://github.com/JakeWard98/jward-helpdesk`
   - Reference: `refs/heads/main`
   - Compose path: `docker-compose.yml`
2. Paste the variables from [`.env.example`](../.env.example) into the
   **Environment variables** pane. The compose file references them explicitly
   rather than through `env_file`, so a git-backed stack works without a `.env`
   on disk.
3. Deploy.

The stack builds two images (`api`, `web`) on first deploy, which takes a few
minutes. `worker` reuses the API image with a different command.

## Services

| Service | Role | Networks |
| --- | --- | --- |
| `db` | PostgreSQL 16 | `internal` |
| `api` | FastAPI, serves `/api` | `internal` (no egress — by design) |
| `worker` | IMAP poller + SMTP sender + housekeeping | `internal`, `egress` |
| `web` | nginx: SPA + reverse proxy to `api` | `internal`, `edge` |
| `cloudflared` | optional, `--profile tunnel` | `edge`, `egress` |

`internal` and `edge` are both `internal: true`, so only `worker` and
`cloudflared` can reach the outside world. Nothing binds a host port.

## First boot

On startup the API:

1. creates any missing tables and the `ticket_number_seq` sequence;
2. seeds the five default templates, skipping any that already exist;
3. creates the admin from `BOOTSTRAP_ADMIN_EMAIL` / `BOOTSTRAP_ADMIN_PASSWORD`
   — but **only if no admin exists yet**, so leaving the variables set cannot
   silently reset a live account.

The bootstrap admin is flagged `must_change_password`, and (with
`REQUIRE_MFA_FOR_AGENTS=true`) must enrol MFA before reaching any ticket.

Once you are in, remove `BOOTSTRAP_ADMIN_PASSWORD` from the stack environment
and redeploy.

## Health

```bash
docker ps --filter name=helpdesk          # all four should be healthy
docker logs -f helpdesk-worker            # mail polling and sending
docker logs helpdesk-api | tail -50
```

The API healthcheck is `python -m app.healthcheck`, which checks the database
too — a healthy `api` container means Postgres is reachable. `web` answers
`/healthz`.

The GUI has **Admin → Mail**, which shows whether SMTP and IMAP are configured,
how many messages are queued or failed, and when mail last arrived.

## Upgrades

Portainer: **Stacks → jward-helpdesk → Pull and redeploy** (tick *re-pull image
and rebuild*).

Command line:

```bash
# Fedora / bash
cd /path/to/jward-helpdesk
git pull
docker compose build --pull
docker compose up -d
```

```fish
# CachyOS / fish
cd /path/to/jward-helpdesk
git pull
docker compose build --pull; and docker compose up -d
```

Schema changes are additive (`create_all`), so a redeploy picks them up. If a
release ever needs a destructive migration it will say so in its notes.

## Backups

Two volumes matter: `db-data` and `attachments`. The attachments are not in the
database, so a database dump alone is not a backup.

```bash
# Fedora / bash - database dump
docker exec helpdesk-db pg_dump -U "$POSTGRES_USER" "$POSTGRES_DB" \
  | gzip > "helpdesk-$(date +%F).sql.gz"

# attachments
docker run --rm -v jward-helpdesk_attachments:/data -v "$PWD:/backup" \
  alpine tar czf "/backup/attachments-$(date +%F).tar.gz" -C /data .
```

```fish
# CachyOS / fish - database dump
docker exec helpdesk-db pg_dump -U $POSTGRES_USER $POSTGRES_DB \
  | gzip > "helpdesk-"(date +%F)".sql.gz"

# attachments
docker run --rm -v jward-helpdesk_attachments:/data -v $PWD:/backup \
  alpine tar czf "/backup/attachments-"(date +%F)".tar.gz" -C /data .
```

Restore into an empty stack: bring up `db` alone, `gunzip -c … | docker exec -i
helpdesk-db psql -U … -d …`, untar the attachments into the volume, then start
the rest.

Note that the volume prefix (`jward-helpdesk_`) comes from the stack name in
Portainer — check with `docker volume ls`.

## Resetting a lost admin

If you are locked out of every admin account, set `BOOTSTRAP_ADMIN_EMAIL` and
`BOOTSTRAP_ADMIN_PASSWORD` to a *new* address and redeploy. The bootstrap only
runs when no admin exists, so first demote or disable the old one — or, if you
truly cannot get in:

```bash
docker exec -it helpdesk-db psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
  -c "UPDATE users SET role='agent' WHERE role='admin';"
```

then redeploy with the bootstrap variables set. This is deliberately manual.

## Troubleshooting

| Symptom | Where to look |
| --- | --- |
| Container restarts at boot with a config error | `docker logs helpdesk-api` — the production guard names each problem |
| Sign-in page loads, API calls fail | `web` cannot reach `api`; check both are on `internal` |
| "Too many attempts" during testing | The rate limiter is doing its job; wait out `LOGIN_LOCKOUT_SECONDS` or clear `rate_limit_buckets` |
| Emails queue but never send | `docker logs helpdesk-worker`; check `worker` is on the `egress` network |
| Replies create new tickets | [docs/email.md](email.md#replies-create-new-tickets) |
