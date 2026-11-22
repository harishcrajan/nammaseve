"""
agent/escalation.py - Follow-up drafting agent for NammaSeve.

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


def _draft_escalation_message(ticket: dict, days_overdue: int, next_level: int, sent_on: str = "") -> str:
    officer, channel = _LEVEL_TARGETS[next_level]
    tid = ticket.get("ticket_id", "UNKNOWN")
    issue = ticket.get("issue_type", "civic issue").replace("_", " ")
    locality = ticket.get("locality", "the locality")
    agency = ticket.get("primary_agency", "the concerned agency")

    if next_level < 2:
        return (
            f"Dear {officer},\n\n"
            f"I am following up on my complaint sent on {sent_on} (NammaSeve reference {tid}).\n\n"
            f"Issue: {issue.title()} at {locality}\n"
            f"Originally sent to: {agency}\n"
            f"Status: {days_overdue} day(s) since sending - the hazard is still unresolved.\n\n"
            f"As the matter has not been addressed within the stipulated timeline, "
            f"I am escalating to your office. Please ensure immediate action is taken "
            f"and an update is provided within 48 hours.\n\n"
            f"Reference: NammaSeve reference {tid}, original complaint sent on {sent_on}.\n\n"
            f"Regards,\nConcerned Resident, {locality}"
        )
    else:
        return (
            f"UNRESOLVED CIVIC COMPLAINT - {locality.upper()}\n\n"
            f"NammaSeve reference {tid}: {issue.title()}\n"
            f"Sent to {agency} on {sent_on} - {days_overdue} days, still unresolved.\n\n"
            f"I have followed up through the Ward Engineer and Zonal Commissioner "
            f"without resolution. Posting publicly to request action from "
            f"{agency} and the Greater Bengaluru Authority.\n\n"
            f"Please contact {agency} helpline or file your own complaint to add weight.\n"
            f"#BengaluruCivicIssues #NammaSeve #{locality.replace(' ', '')}"
        )


def run_escalation(now: datetime) -> list[dict]:
    """Draft a follow-up for every ticket the resident has sent, at least 7 days after sending."""
    traces = []
    for ticket in get_open_tickets():
        status = ticket.get("status", "")
        if not status.startswith("Sent by you on "):
            continue
        sent_on = status.replace("Sent by you on ", "")
        days_since = (now - datetime.strptime(sent_on, "%Y-%m-%d")).days
        if days_since < 7:
            continue
        current_level = int(ticket.get("escalation_level", 0))
        next_level = min(current_level + 1, 2)
        msg = _draft_escalation_message(ticket, days_since, next_level, sent_on)
        update_ticket(ticket["ticket_id"], escalation_level=next_level, escalation_msg=msg)
        officer, channel = _LEVEL_TARGETS[next_level]
        traces.append({
            "step": "escalation",
            "ticket_id": ticket["ticket_id"],
            "input_summary": f"reference={ticket['ticket_id']}, sent={sent_on}, days_since={days_since}",
            "output": f"Follow-up drafted for level {next_level} ({officer})",
            "duration_ms": 0,
            "escalation_level": next_level,
            "officer": officer,
            "channel": channel,
            "days_overdue": days_since,
            "sent_on": sent_on,
            "subject": f"Follow-up: NammaSeve reference {ticket['ticket_id']} (sent {sent_on})",
            "escalation_message": msg,
        })
    return traces
