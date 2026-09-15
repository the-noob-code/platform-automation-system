"""In-memory fake data, session, and lockout bookkeeping for the legacy
bank mini app.

Nothing here talks to a real bank, database, or external service — it's a
self-contained fixture that resets whenever the process restarts, or on
demand via POST /api/reset.
"""
from __future__ import annotations

import secrets
import time
from dataclasses import dataclass


@dataclass
class Account:
    account_type: str  # "checking" | "savings"
    balance: float


@dataclass
class Transaction:
    date: str
    description: str
    account: str  # "checking" | "savings"
    amount: float  # negative = debit, positive = credit


@dataclass
class Member:
    member_id: str
    name: str
    pin: str
    accounts: dict[str, Account]
    history: list[Transaction]


def _make_members() -> dict[str, Member]:
    return {
        "1001": Member(
            "1001", "John Carter", "4471",
            {"checking": Account("checking", 542_318.40), "savings": Account("savings", 15_000.00)},
            [
                Transaction("2026-09-01", "Payroll Deposit", "checking", 4200.00),
                Transaction("2026-09-03", "Grocery Store", "checking", -128.44),
                Transaction("2026-09-05", "Electric Bill", "checking", -96.10),
                Transaction("2026-09-07", "ATM Withdrawal", "checking", -200.00),
                Transaction("2026-09-09", "Transfer to Savings", "checking", -500.00),
                Transaction("2026-09-09", "Transfer from Checking", "savings", 500.00),
            ],
        ),
        "1002": Member(
            "1002", "Maria Alvarez", "8823",
            {"checking": Account("checking", 12_750.50), "savings": Account("savings", 500.00)},
            [
                Transaction("2026-09-02", "Coffee Shop", "checking", -6.25),
                Transaction("2026-09-04", "Payroll Deposit", "checking", 2100.00),
                Transaction("2026-09-08", "Online Purchase", "checking", -84.99),
            ],
        ),
        "1003": Member(
            "1003", "Wei Zhang", "1190",
            {"checking": Account("checking", 98_000.00), "savings": Account("savings", 250_000.00)},
            [
                Transaction("2026-08-28", "Wire Transfer In", "savings", 50_000.00),
                Transaction("2026-09-06", "Property Tax", "checking", -12_500.00),
            ],
        ),
        "9999": Member(
            "9999", "Zero Balance Test Account", "5566",
            {"checking": Account("checking", 0.00), "savings": Account("savings", 0.00)},
            [],
        ),
    }


MEMBERS: dict[str, Member] = _make_members()

# ---------------------------------------------------------------- sessions --
# token -> {"member_id": str, "last_seen": float}. One active session per
# login is enough for this fixture; logging in again just issues a new
# token (any old one for that member is left to expire on its own).
SESSIONS: dict[str, dict] = {}
SESSION_TTL_SECONDS = 90


def create_session(member_id: str) -> str:
    token = secrets.token_hex(16)
    SESSIONS[token] = {"member_id": member_id, "last_seen": time.time()}
    return token


def get_member_id_for_session(token: str | None) -> str | None:
    if not token:
        return None
    session = SESSIONS.get(token)
    if session is None:
        return None
    if (time.time() - session["last_seen"]) > SESSION_TTL_SECONDS:
        SESSIONS.pop(token, None)
        return None
    return session["member_id"]


def touch_session(token: str) -> None:
    if token in SESSIONS:
        SESSIONS[token]["last_seen"] = time.time()


def destroy_session(token: str | None) -> None:
    if token:
        SESSIONS.pop(token, None)


# -------------------------------------------------------- login lockout --
_failed_attempts: dict[str, int] = {}
_lockout_until: dict[str, float] = {}
LOCKOUT_THRESHOLD = 3
LOCKOUT_SECONDS = 60


def is_locked_out(member_id: str) -> bool:
    until = _lockout_until.get(member_id)
    if until is None:
        return False
    if time.time() > until:
        _lockout_until.pop(member_id, None)
        _failed_attempts.pop(member_id, None)
        return False
    return True


def record_failed_attempt(member_id: str) -> None:
    count = _failed_attempts.get(member_id, 0) + 1
    _failed_attempts[member_id] = count
    if count >= LOCKOUT_THRESHOLD:
        _lockout_until[member_id] = time.time() + LOCKOUT_SECONDS


def clear_failed_attempts(member_id: str) -> None:
    _failed_attempts.pop(member_id, None)
    _lockout_until.pop(member_id, None)


# ------------------------------------------------------------- transactions --

def record_transfer(member: Member, from_account: str, to_account: str, amount: float) -> None:
    today = time.strftime("%Y-%m-%d")
    member.history.append(Transaction(today, f"Transfer to {to_account.title()}", from_account, -amount))
    member.history.append(Transaction(today, f"Transfer from {from_account.title()}", to_account, amount))


def reset_state() -> None:
    """Restore balances/history, clear sessions and lockouts. Handy
    between automated test runs so nothing carries over from a prior
    pass."""
    MEMBERS.clear()
    MEMBERS.update(_make_members())
    SESSIONS.clear()
    _failed_attempts.clear()
    _lockout_until.clear()
