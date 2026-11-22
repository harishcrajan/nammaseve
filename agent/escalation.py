"""
agent/escalation.py - Autonomous escalation agent for Ward Fixer.

Escalation levels:
  0 -> Ward Engineer
  1 -> Zonal Commissioner
  2 -> Public social post draft
"""
from datetime import datetime, timedelta
from db.database import get_open_tickets, update_ticket

_ESCALATION_DUE_DAYS = {0: 3, 1: 5, 2: 7}

_LEVEL_TARGETS = {
    0: ("Ward Engineer", "ward office"),
    1: ("Zonal Commissioner", "zone office"),
    2: ("Public (Social Media)", "public post"),
}


def _next_escalation_level(level: int) -> tuple[str, str]:
    next_level = min(level + 1, 2)
    return _LEVEL_TARGETS[next_level]


def _draft_escalation_message(ticket: dict, days_overdue: int, next_level: int) -> str:
    officer, channel = _LEVEL_TARGETS[next_level]
    tid = ticket.get("ticket_id", "UNKNOWN")
    issue = ticket.get("issue_type", "civic issue").replace("_", " ")
    locality = ticket.get("locality", "the locality")
    agency = ticket.get("primary_agency", "the concerned agency")

    if next_level < 2:
        return (
            f"Dear {officer},\n\n"
            f"This is a formal escalation regarding Ticket {tid}.\n\n"
            f"Issue: {issue.title()} at {locality}\n"
            f"Filed with: {agency}\n"
            f"Status: Overdue by {days_overdue} day(s) - no resolution received.\n\n"
            f"As the matter has not been addressed within the stipulated timeline, "
            f"we are escalating to your office. Please ensure immediate action is taken "
            f"and an update is provided within 48 hours.\n\n"
            f"Reference: Ticket {tid} filed on Ward Fixer civic complaint platform.\n\n"
            f"Regards,\nWard Fixer Escalation System"
        )
    else:
        return (
            f"UNRESOLVED CIVIC COMPLAINT - {locality.upper()}\n\n"
            f"Ticket {tid}: {issue.title()}\n"
            f"Filed with {agency} - {days_overdue} days overdue - No response received.\n\n"
            f"We have escalated through the Ward Engineer and Zonal Commissioner "
            f"without resolution. Posting publicly to request action from "
            f"@{agency.replace(' ', '')} and @BBMPBengaluru.\n\n"
            f"Please contact {agency} helpline or file your own complaint to add weight.\n"
            f"#BengaluruCivicIssues #WardFixer #{locality.replace(' ', '')}"
        )


def run_escalation(now: datetime) -> list[dict]:
    """Find all overdue tickets, raise escalation level, draft message."""
    today = now.strftime("%Y-%m-%d")
    all_tickets = get_open_tickets()
    traces = []

    for ticket in all_tickets:
        if ticket.get("status") in ("Resolved",):
            continue
        due_date_str = ticket.get("due_date", "")
        if not due_date_str or due_date_str >= today:
            continue

        due_date = datetime.strptime(due_date_str, "%Y-%m-%d")
        days_overdue = (now - due_date).days
        current_level = int(ticket.get("escalation_level", 0))
        next_level = min(current_level + 1, 2)

        msg = _draft_escalation_message(ticket, days_overdue, next_level)
        extra_days = _ESCALATION_DUE_DAYS.get(next_level, 7)
        new_due = (now + timedelta(days=extra_days)).strftime("%Y-%m-%d")

        update_ticket(
            ticket["ticket_id"],
            escalation_level=next_level,
            due_date=new_due,
            escalation_msg=msg,
            status="Escalated",
        )

        officer, channel = _LEVEL_TARGETS[next_level]
        traces.append({
            "step": "escalation",
            "ticket_id": ticket["ticket_id"],
            "input_summary": f"ticket={ticket['ticket_id']}, days_overdue={days_overdue}, prev_level={current_level}",
            "output": f"Escalated to level {next_level} ({officer}), new due={new_due}",
            "duration_ms": 0,
            "escalation_level": next_level,
            "officer": officer,
            "channel": channel,
            "new_due_date": new_due,
            "days_overdue": days_overdue,
            "escalation_message": msg,
        })

    return traces
