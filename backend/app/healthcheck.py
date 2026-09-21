"""Container healthcheck.

Run as ``python -m app.healthcheck``. Exits 0 when the API answers and the
database is reachable. Kept as a python module so the runtime image needs no
curl or wget.
"""

from __future__ import annotations

import sys

import httpx


def main() -> int:
    try:
        response = httpx.get("http://127.0.0.1:8000/api/health", timeout=5.0)
    except httpx.HTTPError as exc:
        print(f"healthcheck: request failed: {exc}", file=sys.stderr)
        return 1

    if response.status_code != 200:
        print(f"healthcheck: status {response.status_code}", file=sys.stderr)
        return 1

    payload = response.json()
    if payload.get("database") != "ok":
        print(f"healthcheck: database not ok: {payload}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
