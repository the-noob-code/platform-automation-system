"""
Legacy Bank Mini App
=====================
A deliberately hostile local test fixture for exercising browser-automation
/ agentic discovery engines (e.g. an Ollama-driven planner that leans on
Qdrant for semantic element matching instead of clean CSS selectors).

Flow:
    1. GET  /login                    Member ID + PIN
    2. GET  /dashboard                account summary + nav (requires session)
    3. GET  /transfer                 move funds between own accounts
    4. GET  /confirm                  "Transaction Successful" checkpoint
    5. GET  /history                  read-only ledger, loaded via AJAX

Session: a same-origin cookie (`sid`) issued on login, checked server-side
on every authenticated page/route — no id/name/data-testid needed for the
automation to "see" it, since a real browser just carries cookies.

Deliberate legacy constraints (unchanged from the original spec):
    - No id / name / data-testid attributes anywhere in the markup.
    - Layout is nested <table>/<tr>/<td>, not flexbox/grid.
    - No native <form> submissions — every screen wires its own fetch()
      calls by hand (a native form couldn't carry field values anyway
      without `name` attributes).

Embedded runtime traps:
    - Member ID "0000" always resolves to "Account Not Found" (business
      rule, not a crash).
    - Wrong PIN -> "Invalid PIN"; 3 wrong PINs in a row -> account
      locked for 60s ("Account Locked — Too Many Attempts", HTTP 423).
    - POST /api/transfer sleeps a random 3-5s before responding.
    - The Transfer button's client JS has a random chance of firing a
      blocking `alert('Manager Override Required')` instead of
      submitting at all.
    - A small random chance of a genuine *unhandled* exception (a true
      500 hard crash) on transfer, deliberately separate from the
      business-rule branches above.
    - An idle session (no authenticated request in SESSION_TTL_SECONDS)
      makes any authenticated route bounce to /login (pages) or return a
      session-expired error, HTTP 440 (fetch calls) instead of quietly
      failing.
    - GET /api/history has a small random chance of a transient "Unable
      to Load History" business error, to exercise a read-only screen
      whose content loads asynchronously after the page itself.

Run:
    uv sync
    uv run uvicorn app.main:app --reload --port 8000

Tuning knobs (env vars): CHAOS_MODE, ALERT_PROBABILITY, CRASH_PROBABILITY,
HISTORY_ERROR_PROBABILITY (see README). Debug utilities: GET /api/state,
POST /api/reset.
"""
from __future__ import annotations

import asyncio
import os
import random
import re
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from app.data import (
    MEMBERS,
    clear_failed_attempts,
    create_session,
    destroy_session,
    get_member_id_for_session,
    is_locked_out,
    record_failed_attempt,
    record_transfer,
    reset_state,
    touch_session,
)

BASE_DIR = Path(__file__).resolve().parent

CHAOS_MODE = os.environ.get("CHAOS_MODE", "1") != "0"
ALERT_PROBABILITY = float(os.environ.get("ALERT_PROBABILITY", "0.3"))
CRASH_PROBABILITY = float(os.environ.get("CRASH_PROBABILITY", "0.07"))
HISTORY_ERROR_PROBABILITY = float(os.environ.get("HISTORY_ERROR_PROBABILITY", "0.12"))

app = FastAPI(title="Legacy Bank Mini App")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
ACCOUNT_TYPES = {"checking", "savings"}


class LoginRequest(BaseModel):
    member_id: str
    pin: str


class TransferRequest(BaseModel):
    from_account: str
    to_account: str
    amount: str  # kept as str so bad input is a validation branch, not a 422


def _authenticated_member_id(request: Request) -> str | None:
    token = request.cookies.get("sid")
    member_id = get_member_id_for_session(token)
    if member_id:
        touch_session(token)
    return member_id


# ------------------------------------------------------------------ login --

@app.get("/", response_class=HTMLResponse)
def root(request: Request):
    if _authenticated_member_id(request):
        return RedirectResponse("/dashboard")
    return RedirectResponse("/login")

@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    return templates.TemplateResponse(
        request,
        "login.html",
        {"request": request},
    )

@app.get("/search", response_class=HTMLResponse)
def search_page(request: Request):
    return templates.TemplateResponse(
        request,
        "search.html",
        {"request": request},
    )


@app.post("/api/login")
def login(payload: LoginRequest):
    member_id = payload.member_id.strip()
    pin = payload.pin.strip()

    # Hardcoded business rule, unchanged from the original spec: this ID
    # is always "not found", regardless of what's in the member table.
    if member_id == "0000":
        return JSONResponse({"status": "not_found", "message": "Account Not Found"})

    if not re.fullmatch(r"\d{4}", member_id):
        return JSONResponse(
            {"status": "invalid", "message": "Invalid Member ID Format"}, status_code=400
        )

    member = MEMBERS.get(member_id)
    if member is None:
        return JSONResponse({"status": "not_found", "message": "Account Not Found"})

    if is_locked_out(member_id):
        return JSONResponse(
            {"status": "locked", "message": "Account Locked \u2014 Too Many Attempts"},
            status_code=423,
        )

    if pin != member.pin:
        record_failed_attempt(member_id)
        return JSONResponse({"status": "invalid_pin", "message": "Invalid PIN"}, status_code=401)

    clear_failed_attempts(member_id)
    token = create_session(member_id)
    response = JSONResponse({"status": "ok", "redirect": "/dashboard"})
    response.set_cookie("sid", token, httponly=True, samesite="lax")
    return response


@app.post("/api/logout")
def logout(request: Request):
    destroy_session(request.cookies.get("sid"))
    response = JSONResponse({"status": "ok", "redirect": "/login"})
    response.delete_cookie("sid")
    return response


# -------------------------------------------------------------- dashboard --

@app.get("/dashboard", response_class=HTMLResponse)
def dashboard(request: Request):
    member_id = _authenticated_member_id(request)
    if member_id is None:
        return RedirectResponse("/login")
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "request": request,
            "member": MEMBERS[member_id],
        },
    )


# --------------------------------------------------------------- transfer --

@app.get("/transfer", response_class=HTMLResponse)
def transfer_page(request: Request):
    member_id = _authenticated_member_id(request)
    if member_id is None:
        return RedirectResponse("/login")
    return templates.TemplateResponse(
        request,
        "transfer.html",
        {
            "request": request,
            "member": MEMBERS[member_id],
            "chaos_mode": CHAOS_MODE,
            "alert_probability": ALERT_PROBABILITY,
        },
    )


@app.post("/api/transfer")
async def transfer(request: Request, payload: TransferRequest, bypass_chaos: bool = False):
    token = request.cookies.get("sid")
    member_id = get_member_id_for_session(token)
    if member_id is None:
        return JSONResponse(
            {"status": "session_expired", "message": "Session Expired \u2014 Please Log In Again"},
            status_code=440,
        )
    member = MEMBERS[member_id]

    # --- business-rule validation: bad input is never a crash ---
    if payload.from_account not in ACCOUNT_TYPES or payload.to_account not in ACCOUNT_TYPES:
        return JSONResponse(
            {"status": "invalid_accounts", "message": "Invalid Account Selection"}, status_code=400
        )
    if payload.from_account == payload.to_account:
        return JSONResponse(
            {"status": "invalid_accounts", "message": "From and To accounts must be different"},
            status_code=400,
        )

    try:
        amount = float(payload.amount)
    except ValueError:
        return JSONResponse(
            {"status": "invalid_amount", "message": "Invalid Transfer Amount"}, status_code=400
        )
    if amount <= 0:
        return JSONResponse(
            {"status": "invalid_amount", "message": "Invalid Transfer Amount"}, status_code=400
        )

    from_acc = member.accounts[payload.from_account]
    to_acc = member.accounts[payload.to_account]
    if amount > from_acc.balance:
        return JSONResponse(
            {"status": "insufficient_funds", "message": "Insufficient Funds"}, status_code=400
        )

    # --- recoverable condition: transient backend slowness ---
    await asyncio.sleep(random.uniform(3, 5))

    # --- rare hard crash: a genuine unhandled exception, not a business
    #     rule, kept deliberately separate from the validation branches
    #     above so a replay engine can be exercised on telling the two
    #     apart. ---
    if CHAOS_MODE and not bypass_chaos and random.random() < CRASH_PROBABILITY:
        raise RuntimeError("Simulated legacy backend crash in TransferServlet.doPost()")

    from_acc.balance -= amount
    to_acc.balance += amount
    record_transfer(member, payload.from_account, payload.to_account, amount)
    touch_session(token)

    return JSONResponse(
        {
            "status": "ok",
            "redirect": (
                f"/confirm?from_account={payload.from_account}"
                f"&to_account={payload.to_account}&amount={amount:.2f}"
            ),
        }
    )


# ----------------------------------------------------------------- confirm --

@app.get("/confirm", response_class=HTMLResponse)
def confirm(
    request: Request,
    from_account: str = "checking",
    to_account: str = "savings",
    amount: float = 0.0,
):
    member_id = _authenticated_member_id(request)
    if member_id is None:
        return RedirectResponse("/login")
    return templates.TemplateResponse(
        request,
        "confirm.html",
        {
            "request": request,
            "member": MEMBERS[member_id],
            "from_account": from_account,
            "to_account": to_account,
            "amount": amount,
        },
    )


# ----------------------------------------------------------------- history --

@app.get("/history", response_class=HTMLResponse)
def history_page(request: Request):
    member_id = _authenticated_member_id(request)
    if member_id is None:
        return RedirectResponse("/login")
    return templates.TemplateResponse(request, "history.html", {"request": request})


@app.get("/api/history")
async def api_history(request: Request):
    token = request.cookies.get("sid")
    member_id = get_member_id_for_session(token)
    if member_id is None:
        return JSONResponse(
            {"status": "session_expired", "message": "Session Expired \u2014 Please Log In Again"},
            status_code=440,
        )
    member = MEMBERS[member_id]

    # --- transient, recoverable read failure ---
    if CHAOS_MODE and random.random() < HISTORY_ERROR_PROBABILITY:
        return JSONResponse(
            {"status": "error", "message": "Unable to Load History \u2014 Please Retry"},
            status_code=503,
        )

    await asyncio.sleep(random.uniform(0.5, 1.5))
    touch_session(token)
    transactions = sorted(member.history, key=lambda t: t.date, reverse=True)
    return {
        "status": "ok",
        "transactions": [
            {"date": t.date, "description": t.description, "account": t.account, "amount": t.amount}
            for t in transactions
        ],
    }


# ------------------------------------------------- debug/testing utilities --

@app.get("/api/state")
def state():
    """Ground-truth dump of current balances, so a test harness can assert
    against real server state instead of only the scraped UI."""
    return {
        mid: {
            "name": m.name,
            "checking": m.accounts["checking"].balance,
            "savings": m.accounts["savings"].balance,
        }
        for mid, m in MEMBERS.items()
    }


@app.post("/api/reset")
def reset():
    reset_state()
    return {"status": "reset"}
