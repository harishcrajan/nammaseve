"""
db/database.py - Ward Fixer SQLite database layer.
Database file: ward_fixer.db (created at runtime, gitignored).
"""
import sqlite3
import os
from datetime import datetime, timedelta

DB_PATH = os.environ.get("WF_DB_PATH", "ward_fixer.db")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS tickets (
    ticket_id       TEXT PRIMARY KEY,
    locality        TEXT NOT NULL,
    ward            TEXT NOT NULL DEFAULT '',
    issue_type      TEXT NOT NULL,
    severity        INTEGER NOT NULL,
    description     TEXT NOT NULL DEFAULT '',
    primary_agency  TEXT NOT NULL,
    department      TEXT NOT NULL DEFAULT '',
    channel         TEXT NOT NULL DEFAULT '',
    helpline        TEXT NOT NULL DEFAULT '',
    routing_reason  TEXT NOT NULL DEFAULT '',
    subject         TEXT NOT NULL DEFAULT '',
    body_en         TEXT NOT NULL DEFAULT '',
    body_kn         TEXT NOT NULL DEFAULT '',
    status          TEXT NOT NULL DEFAULT 'Filed',
    due_date        TEXT NOT NULL,
    escalation_level INTEGER NOT NULL DEFAULT 0,
    plus_one_count   INTEGER NOT NULL DEFAULT 0,
    escalation_msg   TEXT NOT NULL DEFAULT '',
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);
"""

_DUE_DAYS = {5: 1, 4: 2, 3: 5, 2: 7, 1: 7}


def get_db() -> sqlite3.Connection:
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con


def init_db() -> None:
    """Create tables if they do not exist."""
    with get_db() as con:
        con.executescript(_SCHEMA)


def _next_ticket_id() -> str:
    today = datetime.utcnow().strftime("%Y%m%d")
    prefix = f"WF-{today}-"
    with get_db() as con:
        row = con.execute(
            "SELECT COUNT(*) AS cnt FROM tickets WHERE ticket_id LIKE ?",
            (f"{prefix}%",),
        ).fetchone()
        seq = (row["cnt"] if row else 0) + 1
    return f"{prefix}{seq:03d}"


def insert_ticket(ticket: dict) -> str:
    """Persist a new ticket and return the generated ticket_id."""
    tid = _next_ticket_id()
    severity = int(ticket.get("severity", 3))
    due_days = _DUE_DAYS.get(severity, 7)
    due_date = (datetime.utcnow() + timedelta(days=due_days)).strftime("%Y-%m-%d")
    now_iso = datetime.utcnow().isoformat()

    with get_db() as con:
        con.execute(
            """INSERT INTO tickets
               (ticket_id, locality, ward, issue_type, severity, description,
                primary_agency, department, channel, helpline, routing_reason,
                subject, body_en, body_kn, status, due_date,
                escalation_level, plus_one_count, escalation_msg,
                created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,'Filed',?,0,0,'',?,?)""",
            (
                tid,
                ticket.get("locality", ""),
                ticket.get("ward", ""),
                ticket.get("issue_type", ""),
                severity,
                ticket.get("description", ""),
                ticket.get("primary_agency", ""),
                ticket.get("department", ""),
                ticket.get("channel", ""),
                ticket.get("helpline", ""),
                ticket.get("routing_reason", ""),
                ticket.get("subject", ""),
                ticket.get("body_en", ""),
                ticket.get("body_kn", ""),
                due_date,
                now_iso,
                now_iso,
            ),
        )
    return tid


def get_open_tickets() -> list[dict]:
    with get_db() as con:
        rows = con.execute(
            "SELECT * FROM tickets ORDER BY created_at DESC"
        ).fetchall()
    return [dict(r) for r in rows]


def get_ticket_by_id(ticket_id: str) -> dict | None:
    with get_db() as con:
        row = con.execute(
            "SELECT * FROM tickets WHERE ticket_id = ?", (ticket_id,)
        ).fetchone()
    return dict(row) if row else None


def find_open_duplicate(issue_type: str, locality: str) -> dict | None:
    """Return the first non-resolved ticket matching issue_type + locality."""
    with get_db() as con:
        row = con.execute(
            """SELECT * FROM tickets
               WHERE issue_type = ? AND locality = ? AND status NOT IN ('Resolved')
               ORDER BY created_at DESC LIMIT 1""",
            (issue_type, locality),
        ).fetchone()
    return dict(row) if row else None


def increment_plus_one(ticket_id: str) -> None:
    with get_db() as con:
        con.execute(
            "UPDATE tickets SET plus_one_count = plus_one_count + 1, updated_at = ? WHERE ticket_id = ?",
            (datetime.utcnow().isoformat(), ticket_id),
        )


def update_ticket(ticket_id: str, **kwargs) -> None:
    if not kwargs:
        return
    kwargs["updated_at"] = datetime.utcnow().isoformat()
    sets = ", ".join(f"{k} = ?" for k in kwargs)
    vals = list(kwargs.values()) + [ticket_id]
    with get_db() as con:
        con.execute(f"UPDATE tickets SET {sets} WHERE ticket_id = ?", vals)


def mark_resolved(ticket_id: str) -> None:
    update_ticket(ticket_id, status="Resolved")
