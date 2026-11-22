"""
agent/tools.py - Pipeline steps for Ward Fixer: Who owns this?

Steps (each appends to the live trace):
  analyze_photo -> resolve_ward -> find_duplicates -> attribute_owner ->
  check_permit -> route_agency -> draft_complaint -> file_and_schedule

Each function returns a dict with keys:
  step, input_summary, output, duration_ms
plus the step-specific output fields merged in at the top level.

Issue families: ROAD_CUT, CABLE, OPEN_DRAIN. Anything else (OTHER) takes the
short single-owner path (no attribution or permit check).
"""
import json
import time
from datetime import date
from pathlib import Path

from agent.llm import vision_json, text_json
from db.database import find_open_duplicate, insert_ticket, increment_plus_one, get_ticket_by_id

_DATA_DIR = Path(__file__).parent.parent / "data"

FAMILIES = ("ROAD_CUT", "CABLE", "OPEN_DRAIN")
CABLE_SUBTYPES = ("LIVE_POWER", "TELECOM_OFC", "STREETLIGHT")


def _load_json(filename: str) -> dict:
    with open(_DATA_DIR / filename, encoding="utf-8") as f:
        return json.load(f)


_WARDS: dict = {}
_RULES: dict = {}
_CLUES: dict = {}
_PERMITS: list = []


def _ensure_data() -> None:
    global _WARDS, _RULES, _CLUES, _PERMITS
    if not _WARDS:
        raw = _load_json("wards.json")
        for item in raw["localities"]:
            _WARDS[item["locality"]] = item
    if not _RULES:
        _RULES = _load_json("routing_rules.json")
    if not _CLUES:
        _CLUES = _load_json("evidence_clues.json")
    if not _PERMITS:
        _PERMITS = _load_json("permits.json")["permits"]


def localities() -> list[str]:
    _ensure_data()
    return list(_WARDS.keys())


def _agency_name(key: str, ward: dict) -> str:
    """Display name for a suspect key; CITY_CORP resolves to the ward's corporation."""
    if key == "CITY_CORP":
        return ward.get("corporation", "City Corporation")
    return _CLUES["suspects"].get(key, {}).get("name", key)


def _trace(step: str, input_summary: str, output: str, t0: float, **fields) -> dict:
    return {"step": step, "input_summary": input_summary, "output": output,
            "duration_ms": int((time.time() - t0) * 1000), **fields}


# ---------------------------------------------------------------- 1. analyze_photo

def analyze_photo(image, optional_text: str = "", filename: str = "") -> dict:
    """Identify the hazard family, sub-type and visible ownership clues."""
    _ensure_data()
    t0 = time.time()
    hint = f"{filename} {optional_text}".strip()
    clue_ids = ", ".join(_CLUES["clues"].keys())
    prompt = (
        "You are a civic-hazard classification assistant for Bengaluru, India. "
        "Analyse the image and return ONLY valid JSON with keys: "
        "family (one of ROAD_CUT, CABLE, OPEN_DRAIN, OTHER), "
        "sub_type (ROAD_CUT: TRENCH_PIPE|TRENCH_DUCT|TRENCH_OTHER; CABLE: LIVE_POWER|TELECOM_OFC|STREETLIGHT|UNSURE; "
        "OPEN_DRAIN: SEWER|STORM|UNSURE; OTHER: empty), "
        "issue_type (short snake_case label, only for OTHER e.g. pothole, garbage), "
        "severity (1-5), description (one sentence), confidence (0-1), is_civic_issue (bool), "
        f"visual_clues (list chosen only from: {clue_ids})."
    )
    if optional_text:
        prompt += f"\n\nAdditional context from the resident: {optional_text}"
    result = vision_json(image, prompt, hint=hint)

    family = str(result.get("family", "OTHER")).upper()
    if family not in FAMILIES:
        family = "OTHER"
    result["family"] = family
    result["sub_type"] = str(result.get("sub_type") or "").upper()
    result["visual_clues"] = [c for c in result.get("visual_clues", []) if c in _CLUES["clues"]]
    if family == "OTHER":
        result.setdefault("issue_type", "unknown")
    else:
        result["issue_type"] = f"{family}_{result['sub_type'] or 'UNSURE'}".lower()
    result.setdefault("severity", 3)
    result.setdefault("description", optional_text or "No description provided.")
    result.setdefault("confidence", 0.5)
    result.setdefault("is_civic_issue", True)
    result["severity"] = int(result["severity"])
    return _trace(
        "analyze_photo",
        f"image={'yes' if image is not None else 'no'}, file='{filename}', text='{optional_text[:60]}'",
        f"family={family}, sub_type={result['sub_type'] or '-'}, severity={result['severity']}, "
        f"confidence={float(result['confidence']):.2f}, clues={len(result['visual_clues'])}",
        t0, **result)


# ---------------------------------------------------------------- 2. resolve_ward

def resolve_ward(locality: str) -> dict:
    """Look up ward and city corporation for a locality."""
    _ensure_data()
    t0 = time.time()
    info = _WARDS.get(locality)
    if info:
        ward, zone, corporation = info["ward"], info["zone"], info["corporation"]
    else:
        ward, zone, corporation = f"{locality} Ward (unknown)", "Unknown", "Greater Bengaluru Authority"
    return _trace("resolve_ward", f"locality={locality}", f"ward={ward}, corporation={corporation}", t0,
                  ward=ward, zone=zone, corporation=corporation, locality=locality)


# ---------------------------------------------------------------- 3. find_duplicates

def find_duplicates(issue_type: str, locality: str) -> dict:
    """Check for existing open ticket for the same issue + locality."""
    t0 = time.time()
    existing = find_open_duplicate(issue_type, locality)
    summary = f"issue_type={issue_type}, locality={locality}"
    if existing:
        return _trace("find_duplicates", summary,
                      f"DUPLICATE found: {existing['ticket_id']} (status={existing['status']}, +1={existing['plus_one_count']})",
                      t0, duplicate=existing)
    return _trace("find_duplicates", summary, "No duplicate found - proceeding", t0, duplicate=None)


# ---------------------------------------------------------------- 4. attribute_owner

def attribute_owner(issue: dict, ward: dict) -> dict:
    """Weigh visual clues against data/evidence_clues.json to rank likely owners."""
    _ensure_data()
    t0 = time.time()
    family = issue.get("family", "OTHER")
    evidence, scores = [], {}
    for cid in issue.get("visual_clues", []):
        clue = _CLUES["clues"][cid]
        evidence.append({"clue": cid, "label": clue["label"],
                         "points_to": [_agency_name(k, ward) for k in clue["points_to"]] or ["(no owner signal)"]})
        for key, w in clue["points_to"].items():
            scores[key] = scores.get(key, 0.0) + w

    total = sum(scores.values())
    suspects = sorted(
        ({"key": k, "agency": _agency_name(k, ward), "confidence": round(v / total, 2)} for k, v in scores.items()),
        key=lambda s: s["confidence"], reverse=True) if total else []

    top = suspects[0] if suspects else None
    second = suspects[1] if len(suspects) > 1 else None
    if not top:
        verdict, verdict_key = "UNKNOWN", None
        basis = "No ownership clues visible in the photo."
    elif family == "OPEN_DRAIN" and second and second["confidence"] >= 0.35:
        verdict, verdict_key = "BOTH", "BOTH"
        basis = (f"Clues point to both {top['agency']} ({top['confidence']:.0%}) and "
                 f"{second['agency']} ({second['confidence']:.0%}); sewage is entering a storm drain, so both must act.")
    elif top["confidence"] >= 0.6:
        verdict, verdict_key = top["agency"], top["key"]
        backing = [e["label"] for e in evidence if top["agency"] in e["points_to"]]
        basis = (f"{len(backing)} of {len(evidence)} clues point to {top['agency']}: "
                 + "; ".join(backing) + ".")
    else:
        verdict, verdict_key = "UNCERTAIN", top["key"]
        basis = (f"No single owner is clear (top: {top['agency']} at {top['confidence']:.0%}). "
                 "A joint notice will go to the top suspects.")

    return _trace("attribute_owner", f"family={family}, clues={len(evidence)}",
                  f"verdict={verdict}" + (f" ({top['confidence']:.0%})" if top else ""), t0,
                  evidence=evidence, suspects=suspects, verdict=verdict, verdict_key=verdict_key,
                  verdict_basis=basis)


# ---------------------------------------------------------------- 5. check_permit

def check_permit(issue: dict, ward: dict, attribution: dict, today: date | None = None) -> dict:
    """Check data/permits.json for a road-cutting permit held by the suspected owner."""
    _ensure_data()
    t0 = time.time()
    if issue.get("family") != "ROAD_CUT":
        return _trace("check_permit", f"family={issue.get('family')}", "Not applicable (not a road cut)", t0,
                      permit_status="N/A", permit=None, permit_note="")
    today = today or date.today()
    key = attribution.get("verdict_key")
    if key in (None, "BOTH"):
        key = (attribution.get("suspects") or [{}])[0].get("key")
    agency = _agency_name(key, ward) if key else "unknown agency"
    locality = ward.get("locality", "")
    permit = next((p for p in _PERMITS if p["agency"] == key and p["locality"] == locality), None)

    if permit is None:
        status = "NO_PERMIT"
        note = f"No permit on record for {agency} at {locality} - possible unauthorised cut."
    else:
        restore_by = date.fromisoformat(permit["restore_by"])
        if restore_by < today:
            status = "OVERDUE"
            note = (f"Permit {permit['permit_id']} ({permit['purpose']}) required restoration by "
                    f"{permit['restore_by']} - overdue by {(today - restore_by).days} day(s).")
        else:
            status = "WATCHING"
            note = (f"Permit {permit['permit_id']} ({permit['purpose']}) allows work until "
                    f"{permit['restore_by']}. Watching - not yet due.")
    return _trace("check_permit", f"agency={agency}, locality={locality}", f"{status}: {note}", t0,
                  permit_status=status, permit=permit, permit_note=note)


# ---------------------------------------------------------------- 6. route_agency

def _suspect_contact(key: str, ward: dict) -> dict:
    s = _CLUES["suspects"].get(key, {})
    return {"agency": _agency_name(key, ward), "helpline": s.get("helpline", ""), "channel": s.get("channel", "")}


def route_agency(issue: dict, ward: dict, attribution: dict | None = None, permit: dict | None = None) -> dict:
    """Decide the responsible agency from family logic (or the LLM for the single-owner path)."""
    _ensure_data()
    t0 = time.time()
    family = issue.get("family", "OTHER")
    attribution = attribution or {}
    permit = permit or {}
    corp = ward.get("corporation", "City Corporation")
    safety_banner = False
    severity = int(issue.get("severity", 3))

    if family == "CABLE":
        sub = issue.get("sub_type", "")
        was_unsure = sub not in CABLE_SUBTYPES or issue.get("sub_type_was_unsure", False)
        if sub not in CABLE_SUBTYPES:
            sub = "LIVE_POWER"  # unsure means live power: safest assumption
        issue["sub_type"] = sub
        if sub == "LIVE_POWER":
            c = _suspect_contact("BESCOM", ward)
            result = {"primary_agency": "BESCOM", "cc_agencies": [corp], "department": "Field Operations - Distribution",
                      "reason": ("Cable type is unclear, so it is treated as a live power line; " if was_unsure
                                 else "") + "Hanging power cables are a BESCOM emergency."}
            severity, safety_banner = 5, True
        elif sub == "TELECOM_OFC":
            c = {"agency": corp, "helpline": "1533", "channel": "corporation OFC cell / helpline 1533"}
            result = {"primary_agency": corp, "cc_agencies": ["Telecom operator (OFC)"], "department": "OFC Cell",
                      "reason": f"Sagging or unauthorised fibre cables on public poles are removed by the {corp} OFC cell; the operator is notified."}
        else:
            c = {"agency": corp, "helpline": "1533", "channel": "corporation helpline 1533"}
            result = {"primary_agency": corp, "cc_agencies": [], "department": "Electrical Division (Streetlights)",
                      "reason": f"Streetlight cables are maintained by the {corp} electrical division."}
        result.update(helpline=c["helpline"], channel=c["channel"])

    elif family == "OPEN_DRAIN":
        verdict = attribution.get("verdict_key")
        if verdict == "BOTH":
            sub = "BOTH"
        elif verdict == "BWSSB" or (verdict is None and issue.get("sub_type") == "SEWER"):
            sub = "SEWER"
        else:
            sub = "STORM"
        issue["sub_type"] = sub
        if sub == "SEWER":
            result = {"primary_agency": "BWSSB", "cc_agencies": [], "department": "Sewerage Operations Division",
                      "helpline": "1916", "channel": "bwssb.gov.in / helpline 1916",
                      "reason": "An open sewer manhole is BWSSB's responsibility as the sewer operator."}
        elif sub == "STORM":
            result = {"primary_agency": corp, "cc_agencies": [], "department": "Storm Water Drain Division",
                      "helpline": "1533", "channel": "corporation helpline 1533",
                      "reason": f"Open storm-water drains are maintained by the {corp} storm water drain division."}
        else:
            result = {"primary_agency": "BWSSB", "cc_agencies": [corp], "department": "Sewerage Operations + Storm Water Drain",
                      "helpline": "1916", "channel": "bwssb.gov.in / helpline 1916",
                      "reason": f"Sewage is flowing into a storm drain: BWSSB must stop the sewage and {corp} must cover the drain."}

    elif family == "ROAD_CUT":
        key = attribution.get("verdict_key")
        if key in (None, "BOTH") or attribution.get("verdict") == "UNCERTAIN":
            key = (attribution.get("suspects") or [{"key": "CITY_CORP"}])[0]["key"]
        suspect = _agency_name(key, ward)
        if key in ("BWSSB", "BESCOM"):
            c = _suspect_contact(key, ward)
            result = {"primary_agency": suspect, "cc_agencies": [corp], "department": "Road restoration",
                      "helpline": c["helpline"], "channel": c["channel"],
                      "reason": f"Evidence points to {suspect} as the agency that dug the road; {corp} owns the road and is copied."}
        else:
            cc = [] if key == "CITY_CORP" else [suspect]
            result = {"primary_agency": corp, "cc_agencies": cc, "department": "Roads & road-cutting permissions",
                      "helpline": "1533", "channel": "corporation helpline 1533",
                      "reason": (f"Evidence points to {suspect}; " if cc else "")
                      + f"{corp} owns the road, grants road-cutting permission and can make the cutter restore it."}

    else:
        issue_type = issue.get("issue_type", "unknown")
        prompt = (
            "You are a civic routing assistant for Bengaluru, India. "
            "Return ONLY valid JSON with keys: primary_agency, cc_agencies (list), department, channel, helpline, reason. "
            "The reason must be ONE plain sentence. Use 'City Corporation' for the ward's corporation.\n\n"
            f"Issue: {json.dumps({'issue_type': issue_type, 'description': issue.get('description', '')})}\n"
            f"Ward: {json.dumps(ward)}\n"
            f"Routing rules: {json.dumps(_RULES.get('issue_to_agency', {}))}"
        )
        result = text_json(prompt)
        result.setdefault("primary_agency", _RULES.get("issue_to_agency", {}).get(issue_type, "City Corporation"))
        result.setdefault("cc_agencies", [])
        result.setdefault("department", "General Department")
        result.setdefault("channel", "helpline 1533")
        result.setdefault("helpline", "1533")
        result.setdefault("reason", f"Routed to {result['primary_agency']} based on issue type.")
        for k in ("primary_agency", "reason"):
            result[k] = str(result[k]).replace("City Corporation", corp).replace("BBMP", corp)
        result["cc_agencies"] = [str(a).replace("City Corporation", corp).replace("BBMP", corp) for a in result["cc_agencies"]]

    issue["severity"] = severity
    result.update(severity=severity, safety_banner=safety_banner, sub_type=issue.get("sub_type", ""))
    return _trace("route_agency", f"family={family}, sub_type={issue.get('sub_type') or '-'}",
                  f"primary={result['primary_agency']}, cc={result['cc_agencies']}", t0, **result)


# ---------------------------------------------------------------- 7. draft_complaint

_ISSUE_READABLE = {
    "road_cut": "Unrestored Road Cut", "cable_live_power": "Hanging Live Power Cable",
    "cable_telecom_ofc": "Sagging Telecom / Fibre Cable", "cable_streetlight": "Loose Streetlight Cable",
    "open_drain_sewer": "Open Sewer Manhole", "open_drain_storm": "Open Storm-Water Drain",
    "open_drain_both": "Open Drain With Sewage Inflow",
    "pothole": "Pothole / Road Damage", "garbage": "Garbage Dump / Black Spot",
    "fallen_tree": "Fallen Tree / Branch", "traffic_signal": "Traffic Signal Fault", "unknown": "Civic Issue",
}
_ISSUE_READABLE_KN = {
    "road_cut": "ಮರುಸ್ಥಾಪಿಸದ ರಸ್ತೆ ಅಗೆತ",
    "cable": "ನೇತಾಡುವ ಕೇಬಲ್",
    "open_drain": "ತೆರೆದ ಚರಂಡಿ",
}

_RISK_BY_SEVERITY = {
    5: "an immediate life-threatening emergency", 4: "a serious public health risk",
    3: "a significant safety hazard", 2: "a nuisance and health concern", 1: "a minor inconvenience",
}


def _readable_key(issue: dict) -> str:
    family = issue.get("family", "OTHER")
    if family == "ROAD_CUT":
        return "road_cut"
    if family in FAMILIES:
        return f"{family}_{issue.get('sub_type', '')}".lower()
    return issue.get("issue_type", "unknown")


def draft_complaint(issue: dict, ward: dict, routing: dict,
                    attribution: dict | None = None, permit: dict | None = None) -> dict:
    """Draft the dual notice: formal complaint in English and Kannada."""
    t0 = time.time()
    attribution = attribution or {}
    permit = permit or {}
    key = _readable_key(issue)
    readable = _ISSUE_READABLE.get(key, key.replace("_", " ").title())
    readable_kn = _ISSUE_READABLE_KN.get(issue.get("family", "").lower(), readable)
    locality = ward.get("locality", "the locality")
    ward_name = ward.get("ward", "the ward")
    severity = int(routing.get("severity", issue.get("severity", 3)))
    description = issue.get("description", "")
    agency = routing.get("primary_agency", "the concerned agency")
    dept = routing.get("department", "")
    cc = routing.get("cc_agencies", [])
    risk = _RISK_BY_SEVERITY.get(severity, "a public concern")
    evidence = "; ".join(e["label"] for e in attribution.get("evidence", []))
    corp = ward.get("corporation", "City Corporation")
    corp_addr = "The Commissioner, concerned City Corporation, Greater Bengaluru Authority"
    to_line = corp_addr if agency == corp else f"{agency}{(' - ' + dept) if dept else ''}"
    cc_line = ", ".join(corp_addr if a == corp else a for a in cc)
    permit_q = ("Please confirm whether a road-cutting permit was issued for this location and its restoration due date."
                if issue.get("family") == "ROAD_CUT" else "")

    extra_en = ""
    if evidence:
        extra_en += f"What I observed: {evidence}.\n"
    if permit_q:
        extra_en += f"To the City Corporation: {permit_q}\n"
    if routing.get("safety_banner"):
        extra_en += "SAFETY: Treat as live. Please isolate supply and make safe immediately.\n"

    subject = f"Complaint: {readable} at {locality} - Severity {severity}/5"
    subject_kn = f"ದೂರು: {readable_kn} - {locality} - ತೀವ್ರತೆ {severity}/5"
    body_en = (
        f"To: {to_line}\n"
        + (f"CC: {cc_line}\n" if cc else "")
        + f"\nDear Sir/Madam,\n\nI am writing to bring to your attention a civic hazard at {locality} ({ward_name}).\n\n"
        f"Issue: {readable}\nSeverity: {severity}/5 - {risk}.\nDetails: {description}\n{extra_en}\n"
        f"This matter requires urgent attention by {agency}{(' - ' + dept) if dept else ''}. "
        f"Kindly arrange for prompt rectification and provide a resolution update.\n\nYours sincerely,\nConcerned Resident, {locality}"
    )
    extra_kn = ""
    if evidence:
        extra_kn += f"ಸಾಕ್ಷ್ಯ: {evidence}\n"
    if permit_q:
        extra_kn += "ನಗರ ಪಾಲಿಕೆಗೆ: ಈ ಸ್ಥಳದಲ್ಲಿ ರಸ್ತೆ ಅಗೆಯಲು ಅನುಮತಿ ನೀಡಲಾಗಿದೆಯೇ ಮತ್ತು ಮರುಸ್ಥಾಪನೆಯ ಗಡುವು ಯಾವುದು ಎಂದು ದಯವಿಟ್ಟು ದೃಢಪಡಿಸಿ.\n"
    if routing.get("safety_banner"):
        extra_kn += "ಎಚ್ಚರಿಕೆ: ವಿದ್ಯುತ್ ಸಂಪರ್ಕವಿರಬಹುದು. ತಕ್ಷಣ ಸರಬರಾಜು ನಿಲ್ಲಿಸಿ.\n"
    body_kn = (
        f"ಗೆ: {agency}\n"
        + (f"CC: {', '.join(cc)}\n" if cc else "")
        + f"\nಮಾನ್ಯರೇ,\n\n{locality} ({ward_name}) "
        f"ಪ್ರದೇಶದಲ್ಲಿ ನಾಗರಿಕ "
        f"ಸಮಸ್ಯೆ ವರದಿ ಮಾಡಲು ಬರೆಯುತ್ತಿದ್ದೇನೆ.\n\n"
        f"ಸಮಸ್ಯೆ: {readable_kn}\nತೀವ್ರತೆ: {severity}/5\n"
        f"ವಿವರ: {description}\n{extra_kn}\n"
        f"ಈ ಸಮಸ್ಯೆಯನ್ನು {agency} ತಕ್ಷಣ ಪರಿಹರಿಸಬೇಕೆಂದು ವಿನಂತಿಸುತ್ತೇನೆ.\n\n"
        f"ವಂದನೆಗಳೊಂದಿಗೆ,\nಸ್ಥಳೀಯ ನಿವಾಸಿ, {locality}"
    )
    return _trace("draft_complaint", f"issue={key}, locality={locality}",
                  f"dual notice drafted (EN + KN), subject='{subject[:60]}'", t0,
                  subject=subject, subject_kn=subject_kn, body_en=body_en, body_kn=body_kn)


# ---------------------------------------------------------------- 8. file_and_schedule

def file_and_schedule(issue: dict, ward: dict, routing: dict, complaint: dict,
                      attribution: dict | None = None, permit: dict | None = None) -> dict:
    """Save ticket to SQLite and return ticket ID and due date (simulated filing)."""
    t0 = time.time()
    attribution = attribution or {}
    permit = permit or {}
    watching = permit.get("permit_status") == "WATCHING"
    ticket_data = {
        "locality": ward.get("locality", ""), "ward": ward.get("ward", ""),
        "issue_type": issue.get("issue_type", "unknown"), "severity": routing.get("severity", issue.get("severity", 3)),
        "description": issue.get("description", ""), "primary_agency": routing.get("primary_agency", ""),
        "department": routing.get("department", ""), "channel": routing.get("channel", ""),
        "helpline": routing.get("helpline", ""), "routing_reason": routing.get("reason", ""),
        "subject": complaint.get("subject", ""), "body_en": complaint.get("body_en", ""),
        "body_kn": complaint.get("body_kn", ""),
        "family": issue.get("family", "OTHER"), "sub_type": issue.get("sub_type", ""),
        "verdict": attribution.get("verdict", ""), "permit_status": permit.get("permit_status", ""),
        "cc_agencies": ", ".join(routing.get("cc_agencies", [])),
        "status": "Draft ready",
        "due_date": (permit.get("permit") or {}).get("restore_by") if watching else None,
    }
    ticket_id = insert_ticket(ticket_data)
    saved = get_ticket_by_id(ticket_id) or {}
    return _trace("file_and_schedule",
                  f"issue_type={issue.get('issue_type')}, severity={ticket_data['severity']}",
                  f"NammaSeve reference={ticket_id} (local only), status={saved.get('status')}", t0,
                  ticket_id=ticket_id, status=saved.get("status", "Draft ready"),
                  due_date=saved.get("due_date", ""), escalation_level=0)


# ---------------------------------------------------------------- orchestrator

def run_agent(image, locality: str, description: str = "", filename: str = "", on_step=None) -> dict:
    """Run the full pipeline. on_step(trace) is called after every step (live trace).

    Returns {"traces", "outcome", ...step results}. outcome is one of:
    not_civic, low_confidence, duplicate, filed.
    """
    traces: list[dict] = []

    def emit(t: dict) -> dict:
        traces.append(t)
        if on_step:
            on_step(t)
        return t

    issue = emit(analyze_photo(image, description, filename=filename))
    if not issue.get("is_civic_issue", True):
        return {"outcome": "not_civic", "traces": traces, "issue": issue}
    if float(issue.get("confidence", 1.0)) < 0.6:
        return {"outcome": "low_confidence", "traces": traces, "issue": issue}
    if issue["family"] == "CABLE" and issue["sub_type"] not in CABLE_SUBTYPES:
        issue["sub_type_was_unsure"] = True

    ward = emit(resolve_ward(locality))
    dup = emit(find_duplicates(issue["issue_type"], locality))
    if dup.get("duplicate"):
        increment_plus_one(dup["duplicate"]["ticket_id"])
        return {"outcome": "duplicate", "traces": traces, "issue": issue, "ward": ward,
                "duplicate": get_ticket_by_id(dup["duplicate"]["ticket_id"])}

    attribution, permit = {}, {}
    if issue["family"] in FAMILIES:
        attribution = emit(attribute_owner(issue, ward))
    routing = emit(route_agency(issue, ward, attribution, permit))
    complaint = emit(draft_complaint(issue, ward, routing, attribution, permit))
    filing = emit(file_and_schedule(issue, ward, routing, complaint, attribution, permit))
    return {"outcome": "filed", "traces": traces, "issue": issue, "ward": ward, "attribution": attribution,
            "permit": permit, "routing": routing, "complaint": complaint, "filing": filing}


# ---------------------------------------------------------------- guided inputs (no AI)

HAZARDS = ["Road or footpath dug up", "Cable or wire", "Open drain or manhole"]
TRENCH_OPTIONS = {"large water or sewer pipe": ["blue_hdpe_pipe"], "thick power cable": ["red_warning_tiles"],
                  "bundle of thin ducts": ["orange_duct_bundle"], "yellow gas pipe": ["yellow_gas_pipe"],
                  "metro barricades": ["metro_barricades"], "nothing visible": ["no_barricade_or_board"]}
CABLE_WHERE = {"hanging from an electric pole": ["pole_transformer_nearby"],
               "lying on footpath": ["cable_on_footpath"], "on a streetlight pole": ["streetlight_pole_cable"]}
CABLE_LIVE = {"yes": ["thick_insulated_cable"], "no": ["thin_black_cable_bundle"], "not sure": []}
DRAIN_SHAPE = {"round cover or opening": ["round_manhole_frame"],
               "rectangular slab or roadside channel": ["roadside_channel", "broken_rectangular_slab"]}
DRAIN_SMELL = {"yes": ["grey_black_sewage", "sewage_smell"], "no": ["rainwater_and_silt"]}


def describe_hazard(answers: dict) -> dict:
    """Turn the resident's guided answers into an issue with evidence clues."""
    t0 = time.time()
    hazard = answers["hazard"]
    reported = []
    if hazard == HAZARDS[0]:
        family, clues = "ROAD_CUT", list(TRENCH_OPTIONS[answers["trench"]])
        reported.append(f"in the trench: {answers['trench']}")
        sub, severity = "TRENCH", 3
    elif hazard == HAZARDS[1]:
        family = "CABLE"
        clues = CABLE_WHERE[answers["where"]] + CABLE_LIVE[answers["live"]]
        reported += [f"cable location: {answers['where']}", f"sparking / burning smell / thick insulated: {answers['live']}"]
        if answers["live"] == "no":
            sub = "STREETLIGHT" if answers["where"] == "on a streetlight pole" else "TELECOM_OFC"
        else:
            sub = "LIVE_POWER" if answers["live"] == "yes" else "UNSURE"
        severity = 5 if sub in ("LIVE_POWER", "UNSURE") else (3 if sub == "STREETLIGHT" else 2)
    else:
        family = "OPEN_DRAIN"
        clues = DRAIN_SHAPE[answers["shape"]] + DRAIN_SMELL[answers["smell"]]
        reported += [f"looks like: {answers['shape']}", f"sewage smell or flow: {answers['smell']}"]
        sub, severity = ("SEWER" if answers["smell"] == "yes" else "STORM"), 4
    issue = {"family": family, "sub_type": sub, "issue_type": f"{family}_{sub}".lower(), "severity": severity,
             "visual_clues": clues, "reported": reported, "confidence": 1.0, "is_civic_issue": True,
             "description": f"{hazard}. Reported: " + "; ".join(reported) + "."}
    if sub == "UNSURE":
        issue["sub_type_was_unsure"] = True
    return _trace("describe_hazard", "; ".join(reported), f"family={family}, sub_type={sub}", t0, **issue)


def prefill_from_photo(image) -> dict:
    """Ask the vision model for a first guess at the guided answers (only used when AI is on)."""
    res = analyze_photo(image)
    clues = set(res.get("visual_clues", []))
    pick = lambda table, default: next((k for k, v in table.items() if clues & set(v)), default)
    fam = {"ROAD_CUT": HAZARDS[0], "CABLE": HAZARDS[1], "OPEN_DRAIN": HAZARDS[2]}.get(res["family"], HAZARDS[0])
    live = {"LIVE_POWER": "yes", "TELECOM_OFC": "no", "STREETLIGHT": "no"}.get(res.get("sub_type"), "not sure")
    return {"hazard": fam, "trench": pick(TRENCH_OPTIONS, "nothing visible"),
            "where": pick(CABLE_WHERE, "hanging from an electric pole"), "live": live,
            "shape": pick(DRAIN_SHAPE, "round cover or opening"), "smell": "yes" if res.get("sub_type") == "SEWER" else "no"}


def run_guided(answers: dict, locality: str, landmark: str = "", on_step=None) -> dict:
    """Guided pipeline: describe_hazard -> find_duplicates -> attribute_owner -> route_agency ->
    draft_complaint -> file_and_schedule (saved locally as a draft)."""
    traces: list[dict] = []

    def emit(t: dict) -> dict:
        traces.append(t)
        if on_step:
            on_step(t)
        return t

    issue = emit(describe_hazard(answers))
    place = f"{landmark}, {locality}" if landmark else locality
    ward = {"locality": place, "ward": "Bengaluru", "corporation": "City Corporation"}
    dup = emit(find_duplicates(issue["issue_type"], place))
    if dup.get("duplicate"):
        increment_plus_one(dup["duplicate"]["ticket_id"])
        return {"outcome": "duplicate", "traces": traces, "issue": issue, "ward": ward,
                "duplicate": get_ticket_by_id(dup["duplicate"]["ticket_id"])}
    attribution = emit(attribute_owner(issue, ward))
    routing = emit(route_agency(issue, ward, attribution))
    complaint = emit(draft_complaint(issue, ward, routing, attribution))
    filing = emit(file_and_schedule(issue, ward, routing, complaint, attribution))
    return {"outcome": "filed", "traces": traces, "issue": issue, "ward": ward, "attribution": attribution,
            "routing": routing, "complaint": complaint, "filing": filing}
