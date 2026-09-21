# Contributing

Personal project, but the door is open. If you are sending a change:

## Before you start

- Open an issue first for anything bigger than a fix. It saves both of us time.
- Read [CLAUDE.md](CLAUDE.md) — it is written for an AI assistant but it is the
  real style guide.

## Ground rules

- **No secrets, ever.** The repository is public. Use `example.com` /
  `example.org` in code, tests, docs and screenshots.
- **Security changes need tests.** Anything touching authentication, session
  handling, email routing, uploads or HTML sanitising needs a test that would
  fail without the change. The suite already covers the adversarial cases —
  follow that pattern.
- Keep changes focused. One concern per pull request.

## Checks

Run these before pushing:

```bash
cd backend && pytest && ruff check .
cd frontend && npm run build
docker compose --env-file .env config -q
```

CI runs the same things.

## Commits

Write the message for someone reading `git log` in a year: what changed and
why, not a restatement of the diff. Present tense, no ticket-number prefixes.
