"""
agent/tools.py - Six pipeline steps for Ward Fixer.

Each function returns a dict with keys:
  step, input_summary, output, duration_ms
plus the step-specific output fields merged in at the top level.
"""
import json
import time
from pathlib import Path

from agent.llm import vision_json, text_json
from db.database import find_open_duplicate, insert_ticket

_DATA_DIR = Path(__file__).parent.parent / "data"


def _load_json(filename: str) -> dict:
    with open(_DATA_DIR / filename, encoding="utf-8") as f:
        return json.load(f)


_WARDS: dict = {}
_RULES: dict = {}


def _ensure_data() -> None:
    global _WARDS, _RULES
    if not _WARDS:
        raw = _load_json("wards.json")
        for item in raw["localities"]:
            _WARDS[item["locality"]] = item
    if not _RULES:
        _RULES = _load_json("routing_rules.json")


def analyze_photo(image, optional_text: str = "") -> dict:
    """Identify civic issue from photo + optional text."""
    t0 = time.time()
    hint = optional_text
    if image is not None and hasattr(image, "filename"):
        hint = f"{image.filename} {optional_text}"
    prompt = (
        "You are a civic-issue classification assistant for Bengaluru, India. "
        "Analyse the image and return ONLY valid JSON with keys: "
        "issue_type, severity (1-5), description, confidence (0-1), is_civic_issue (bool)."
    )
    if optional_text:
        prompt += f"\n\nAdditional context: {optional_text}"
    result = vision_json(image, prompt, hint=hint)
    result.setdefault("issue_type", "unknown")
    result.setdefault("severity", 3)
    result.setdefault("description", optional_text or "No description provided.")
    result.setdefault("confidence", 0.5)
    result.setdefault("is_civic_issue", True)
    duration_ms = int((time.time() - t0) * 1000)
    return {
        "step": "analyze_photo",
        "input_summary": f"image={'yes' if image else 'no'}, text='{optional_text[:60]}'",
        "output": f"issue_type={result['issue_type']}, severity={result['severity']}, confidence={result['confidence']:.2f}",
        "duration_ms": duration_ms,
        **result,
    }


def resolve_ward(locality: str) -> dict:
    """Look up ward, zone and corporation for a locality."""
    _ensure_data()
    t0 = time.time()
    info = _WARDS.get(locality)
    if info:
        ward, zone, corporation = info["ward"], info["zone"], info["corporation"]
    else:
        ward, zone, corporation = f"{locality} Ward (unknown)", "Unknown Zone", "BBMP"
    duration_ms = int((time.time() - t0) * 1000)
    return {"step": "resolve_ward", "input_summary": f"locality={locality}",
            "output": f"ward={ward}, zone={zone}", "duration_ms": duration_ms,
            "ward": ward, "zone": zone, "corporation": corporation, "locality": locality}


def find_duplicates(issue_type: str, locality: str) -> dict:
    """Check for existing open ticket for the same issue + locality."""
    t0 = time.time()
    existing = find_open_duplicate(issue_type, locality)
    duration_ms = int((time.time() - t0) * 1000)
    if existing:
        return {"step": "find_duplicates", "input_summary": f"issue_type={issue_type}, locality={locality}",
                "output": f"DUPLICATE found: {existing['ticket_id']} (status={existing['status']}, +1={existing['plus_one_count']})",
                "duration_ms": duration_ms, "duplicate": existing}
    return {"step": "find_duplicates", "input_summary": f"issue_type={issue_type}, locality={locality}",
            "output": "No duplicate found - proceeding", "duration_ms": duration_ms, "duplicate": None}


def route_agency(issue: dict, ward: dict) -> dict:
    """Decide responsible agency using routing rules + LLM."""
    _ensure_data()
    t0 = time.time()
    issue_type = issue.get("issue_type", "unknown")
    prompt = (
        "You are a civic routing assistant for Bengaluru, India. "
        "Return ONLY valid JSON with keys: primary_agency, cc_agencies (list), department, channel, helpline, reason. "
        "The reason must be ONE plain sentence.\n\n"
        f"Issue: {json.dumps(issue)}\nWard: {json.dumps(ward)}\n"
        f"Routing rules: {json.dumps(_RULES.get('issue_to_agency', {}))}\n"
        f"Ambiguous cases: {json.dumps(_RULES.get('ambiguous_cases', []))}"
    )
    result = text_json(prompt)
    result.setdefault("primary_agency", _RULES.get("issue_to_agency", {}).get(issue_type, "BBMP"))
    result.setdefault("cc_agencies", [])
    result.setdefault("department", "General Department")
    result.setdefault("channel", "helpline 1533")
    result.setdefault("helpline", "1533")
    result.setdefault("reason", f"Routed to {result['primary_agency']} based on issue type.")
    duration_ms = int((time.time() - t0) * 1000)
    return {"step": "route_agency", "input_summary": f"issue_type={issue_type}",
            "output": f"primary={result['primary_agency']}, cc={result['cc_agencies']}, dept={result['department']}",
            "duration_ms": duration_ms, **result}


_ISSUE_READABLE = {
    "pothole": "Pothole / Road Damage", "damaged_road": "Damaged Road Surface",
    "garbage": "Garbage Dump / Black Spot", "streetlight": "Non-Functional Streetlight",
    "dangling_wire": "Dangling / Snapped Power Line", "transformer": "Sparking Transformer",
    "water_leak": "Water Pipe Leak", "water_burst": "Water Main Burst",
    "sewage_overflow": "Sewage Overflow", "open_manhole_sewer": "Open Manhole (Sewer)",
    "open_manhole_stormwater": "Open Manhole (Stormwater)", "fallen_tree": "Fallen Tree / Branch",
    "fallen_tree_on_line": "Fallen Tree on Power Line", "road_dug_bwssb": "Road Dug Up (BWSSB Works)",
    "road_dug_bescom": "Road Dug Up (BESCOM Works)", "traffic_signal": "Traffic Signal Fault",
    "road_blocking": "Road Blocking / Encroachment", "unknown": "Civic Issue",
}

_RISK_BY_SEVERITY = {
    5: "an immediate life-threatening emergency", 4: "a serious public health risk",
    3: "a significant safety hazard", 2: "a nuisance and health concern", 1: "a minor inconvenience",
}


def draft_complaint(issue: dict, ward: dict, routing: dict) -> dict:
    """Draft formal complaint in English and Kannada."""
    t0 = time.time()
    issue_type = issue.get("issue_type", "unknown")
    readable = _ISSUE_READABLE.get(issue_type, issue_type.replace("_", " ").title())
    locality = ward.get("locality", "the locality")
    ward_name = ward.get("ward", "the ward")
    severity = int(issue.get("severity", 3))
    description = issue.get("description", "")
    agency = routing.get("primary_agency", "the concerned agency")
    dept = routing.get("department", "")
    risk = _RISK_BY_SEVERITY.get(severity, "a public concern")

    subject = f"Complaint: {readable} at {locality} - Severity {severity}/5"
    body_en = (
        f"Dear Sir/Madam,\n\nI am writing to bring to your attention a civic issue at {locality} ({ward_name}).\n\n"
        f"Issue: {readable}\nSeverity: {severity}/5 - {risk}.\nDetails: {description}\n\n"
        f"This matter requires urgent attention by {agency}{(' - ' + dept) if dept else ''}. "
        f"Kindly arrange for prompt rectification and provide a resolution update.\n\nYours sincerely,\nConcerned Resident, {locality}"
    )
    body_kn = (
        f"\u0cae\u0cbe\u0ca8\u0ccd\u0caf\u0cb0\u0cc7,\n\n{locality} ({ward_name}) "
        f"\u0caa\u0ccd\u0cb0\u0ca6\u0cc7\u0cb6\u0ca6\u0cb2\u0ccd\u0cb2\u0cbf \u0ca8\u0cbe\u0c97\u0cb0\u0cbf\u0c95 "
        f"\u0cb8\u0cae\u0cb8\u0ccd\u0caf\u0cc6 \u0cb5\u0cb0\u0ca6\u0cbf \u0cae\u0cbe\u0ca1\u0cb2\u0cc1 \u0cac\u0cb0\u0cc6\u0caf\u0cc1\u0ca4\u0ccd\u0ca4\u0cbf\u0ca6\u0ccd\u0ca6\u0cc7\u0ca8\u0cc6.\n\n"
        f"\u0cb8\u0cae\u0cb8\u0ccd\u0caf\u0cc6: {readable}\n\u0ca4\u0cc0\u0cb5\u0ccd\u0cb0\u0ca4\u0cc6: {severity}/5\n"
        f"\u0cb5\u0cbf\u0cb5\u0cb0: {description}\n\n"
        f"\u0c88 \u0cb8\u0cae\u0cb8\u0ccd\u0caf\u0cc6\u0caf\u0ca8\u0ccd\u0ca8\u0cc1 {agency} \u0ca4\u0c95\u0ccd\u0cb7\u0ca3 \u0caa\u0cb0\u0cbf\u0cb9\u0cb0\u0cbf\u0cb8\u0cac\u0cc7\u0c95\u0cc6\u0c82\u0ca6\u0cc1 \u0cb5\u0cbf\u0ca8\u0c82\u0ca4\u0cbf\u0cb8\u0cc1\u0ca4\u0ccd\u0ca4\u0cc7\u0ca8\u0cc6.\n\n"
        f"\u0cb5\u0c82\u0ca6\u0ca8\u0cc6\u0c97\u0cb3\u0cca\u0c82\u0ca6\u0cbf\u0c97\u0cc6,\n\u0cb8\u0ccd\u0ca5\u0cb3\u0cc0\u0caf \u0ca8\u0cbf\u0cb5\u0cbe\u0cb8\u0cbf, {locality}"
    )
    duration_ms = int((time.time() - t0) * 1000)
    return {"step": "draft_complaint", "input_summary": f"issue_type={issue_type}, locality={locality}",
            "output": f"subject='{subject[:60]}...'", "duration_ms": duration_ms,
            "subject": subject, "body_en": body_en, "body_kn": body_kn}


def file_and_schedule(issue: dict, ward: dict, routing: dict, complaint: dict) -> dict:
    """Save ticket to SQLite and return ticket ID and due date."""
    t0 = time.time()
    ticket_data = {
        "locality": ward.get("locality", ""), "ward": ward.get("ward", ""),
        "issue_type": issue.get("issue_type", "unknown"), "severity": issue.get("severity", 3),
        "description": issue.get("description", ""), "primary_agency": routing.get("primary_agency", "BBMP"),
        "department": routing.get("department", ""), "channel": routing.get("channel", ""),
        "helpline": routing.get("helpline", ""), "routing_reason": routing.get("reason", ""),
        "subject": complaint.get("subject", ""), "body_en": complaint.get("body_en", ""),
        "body_kn": complaint.get("body_kn", ""),
    }
    ticket_id = insert_ticket(ticket_data)
    from db.database import get_ticket_by_id
    saved = get_ticket_by_id(ticket_id) or {}
    due_date = saved.get("due_date", "")
    duration_ms = int((time.time() - t0) * 1000)
    return {"step": "file_and_schedule", "input_summary": f"issue_type={issue.get('issue_type')}, severity={issue.get('severity')}",
            "output": f"ticket_id={ticket_id}, due={due_date}", "duration_ms": duration_ms,
            "ticket_id": ticket_id, "status": "Filed", "due_date": due_date, "escalation_level": 0}
