# Legacy Bank Mini App

A local, self-contained FastAPI app built as a **test fixture**, not a real
banking system. It exists to give a browser-automation / agentic discovery
engine (e.g. an Ollama-driven planner using Qdrant for semantic element
matching) something realistically hostile to practice on: table-based
1990s-enterprise markup, zero `id`/`name`/`data-testid` attributes, and a
handful of runtime traps that separate "business rule" from "hard crash"
from "needs a human."

All data is in-memory and fake. Nothing here talks to a real bank or moves
real money. State resets on process restart, or on demand via `POST /api/reset`.

## Setup & run

```bash
cd legacy-bank-proxy
uv sync
uv run uvicorn app.main:app --reload --port 8000
```

Then open `http://localhost:8000/`.

You can also start the app with `uv run python -m app`.

## Test accounts

| Member ID | PIN  | Checking     | Savings      |
|-----------|------|--------------|--------------|
| 1001      | 4471 | $542,318.40  | $15,000.00   |
| 1002      | 8823 | $12,750.50   | $500.00      |
| 1003      | 1190 | $98,000.00   | $250,000.00  |
| 9999      | 5566 | $0.00        | $0.00        |

`0000` is hardcoded to always return "Account Not Found," regardless of PIN
— the reproducible business-rule test case from the original spec.

## The flow

| Step | Route | What it does |
|---|---|---|
| 1. Login | `GET /login` | Member ID + PIN |
| 2. Dashboard | `GET /dashboard` | Account summary (checking/savings) + nav |
| 3. Transfer | `GET /transfer` | Move funds between the member's own accounts |
| 4. Checkpoint | `GET /confirm` | "Transaction Successful" — what your engine verifies |
| — | `GET /history` | Read-only ledger, loaded asynchronously after page load |
| — | `POST /api/logout` | Clears the session, redirects to login |

Auth is a same-origin `sid` cookie issued on login and checked server-side
on every authenticated route. A real browser (or a browser-driven agent)
carries it automatically — there's nothing in the DOM to select for it.

## Deliberate legacy constraints

- No `id`, `name`, or `data-testid` anywhere. Inputs/selects are matched
  positionally or via `placeholder` text in the bundled JS — an automation
  stack has to do the same (or lean on semantic matching) since there's
  nothing else to grab onto.
- Layout is nested `<table>`/`<tr>`/`<td>`, no flexbox/grid.
- No native `<form>` submissions (they couldn't carry field values without
  `name` attributes anyway) — each screen wires its own `fetch()` calls by
  hand, same as real legacy JS would.

## Embedded runtime traps

| Trap | Where | Behavior |
|---|---|---|
| Business-rule "not found" | `POST /api/login`, ID `0000` | Always `Account Not Found` — never a crash |
| Malformed ID | `POST /api/login` | Anything not 4 digits → `Invalid Member ID Format` (kept distinct from "not found," on purpose) |
| Wrong PIN | `POST /api/login` | `Invalid PIN` |
| Account lockout | `POST /api/login` | 3 wrong PINs in a row → `Account Locked — Too Many Attempts`, HTTP 423, for 60s |
| Transient slowness | `POST /api/transfer` | Sleeps a random 3–5s before responding |
| Human escalation | Transfer button (client JS) | Random chance of a blocking `alert('Manager Override Required')` instead of submitting |
| Hard crash | `POST /api/transfer` | Small random chance of a genuine unhandled exception → HTTP 500, deliberately distinct from the business-rule branches |
| Session expiry | any authenticated route | Idle > 90s → pages bounce to `/login`; `fetch` calls get `session_expired` / HTTP 440 |
| Invalid amount | `POST /api/transfer` | Non-numeric or ≤0 → `Invalid Transfer Amount` |
| Insufficient funds | `POST /api/transfer` | Amount greater than the source account's balance → `Insufficient Funds` |
| Same-account transfer | `POST /api/transfer` | From/To identical → `From and To accounts must be different` |
| Transient read failure | `GET /api/history` | Small random chance of `Unable to Load History — Please Retry` |

## Tuning knobs (env vars)

```bash
CHAOS_MODE=0 uv run uvicorn app.main:app --port 8000   # disable random alert, crash, and history hiccups
ALERT_PROBABILITY=0.6 uv run uvicorn app.main:app --port 8000
CRASH_PROBABILITY=0.15 uv run uvicorn app.main:app --port 8000
HISTORY_ERROR_PROBABILITY=0.05 uv run uvicorn app.main:app --port 8000
```

Run with `CHAOS_MODE=0` for your discovery/crawl pass so the agent can map
the page structure without tripping the alert, the crash, or the history
hiccup, then switch it back on for resilience testing.
`POST /api/transfer?bypass_chaos=true` does the same for a single transfer
request without touching the env var.

## Debug/testing utilities

Ground-truth helpers, not part of the hostile-UI simulation:

- `GET /api/state` — JSON dump of every member's current checking/savings
  balances, so you can assert against real server state instead of only
  the scraped UI.
- `POST /api/reset` — restores all balances/history and clears sessions
  and lockouts. Handy between automated test runs.

## Manual smoke test

```bash
# login as 1001, keep the cookie jar
curl -s -c cookies.txt -X POST localhost:8000/api/login \
  -H 'Content-Type: application/json' -d '{"member_id":"1001","pin":"4471"}'
# {"status":"ok","redirect":"/dashboard"}

curl -s -b cookies.txt localhost:8000/api/state

curl -s -b cookies.txt -X POST localhost:8000/api/transfer \
  -H 'Content-Type: application/json' \
  -d '{"from_account":"checking","to_account":"savings","amount":"100"}'
# ~3-5s later: {"status":"ok","redirect":"/confirm?from_account=checking&to_account=savings&amount=100.00"}

curl -s -b cookies.txt localhost:8000/api/history

# the "always not found" business rule
curl -s -X POST localhost:8000/api/login \
  -H 'Content-Type: application/json' -d '{"member_id":"0000","pin":"0000"}'
# {"status":"not_found","message":"Account Not Found"}

curl -s -X POST localhost:8000/api/reset
```

