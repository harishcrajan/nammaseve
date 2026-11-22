"""
tests/test_use_cases.py - UC1 through UC14 for Ward Fixer (mock mode).

Run: pytest tests/ -v
All tests run with MOCK_MODE=true (default).
"""
import os
import tempfile
import pytest
from datetime import datetime, timedelta

os.environ["MOCK_MODE"] = "true"
import tempfile as _tempfile
_tmp_db = _tempfile.NamedTemporaryFile(suffix=".db", delete=False)
os.environ["WF_DB_PATH"] = _tmp_db.name

from db.database import init_db
from agent.tools import analyze_photo, resolve_ward, find_duplicates, route_agency, draft_complaint, file_and_schedule
from agent.escalation import run_escalation
from db.database import increment_plus_one, get_ticket_by_id, update_ticket

init_db()


def _analyze_with_hint(hint: str, description: str = "") -> dict:
    import agent.llm as llm_mod
    result = llm_mod.vision_json(None, "", hint=hint + " " + description)
    result.setdefault("issue_type", "unknown")
    result.setdefault("severity", 3)
    result.setdefault("description", description or hint)
    result.setdefault("confidence", 0.9)
    result.setdefault("is_civic_issue", True)
    return result


def test_uc1_pothole():
    step1 = _analyze_with_hint("pothole")
    assert step1["issue_type"] == "pothole"
    assert step1["is_civic_issue"] is True
    step2 = resolve_ward("Indiranagar")
    step4 = route_agency(step1, step2)
    assert step4["primary_agency"] == "BBMP"
    assert "road" in step4["department"].lower() or "infrastructure" in step4["department"].lower()


def test_uc2_garbage():
    step1 = _analyze_with_hint("garbage")
    assert step1["issue_type"] == "garbage"
    step2 = resolve_ward("Koramangala")
    step4 = route_agency(step1, step2)
    assert step4["primary_agency"] == "BBMP"
    assert "waste" in step4["department"].lower()


def test_uc3_streetlight():
    step1 = _analyze_with_hint("streetlight")
    assert step1["issue_type"] == "streetlight"
    step2 = resolve_ward("Jayanagar")
    step4 = route_agency(step1, step2)
    assert step4["primary_agency"] == "BBMP"
    assert "electric" in step4["department"].lower() or "division" in step4["department"].lower()


def test_uc4_dangling_wire():
    step1 = _analyze_with_hint("wire")
    assert step1["issue_type"] == "dangling_wire"
    assert step1["severity"] == 5
    step2 = resolve_ward("Malleshwaram")
    step4 = route_agency(step1, step2)
    assert step4["primary_agency"] == "BESCOM"
    assert step4["helpline"] == "1912"


def test_uc5_water_leak():
    step1 = _analyze_with_hint("leak")
    assert step1["issue_type"] in ("water_leak", "water_burst")
    step2 = resolve_ward("HSR Layout")
    step4 = route_agency(step1, step2)
    assert step4["primary_agency"] == "BWSSB"
    assert step4["helpline"] == "1916"


def test_uc6_sewage_overflow():
    step1 = _analyze_with_hint("sewage")
    assert step1["issue_type"] in ("sewage_overflow", "open_manhole_sewer")
    step2 = resolve_ward("BTM Layout")
    step4 = route_agency(step1, step2)
    assert step4["primary_agency"] == "BWSSB"
    assert len(step4.get("reason", "")) > 0


def test_uc7_road_dug_up():
    step1 = _analyze_with_hint("dug")
    assert step1["issue_type"] in ("road_dug_bwssb", "road_dug_bescom")
    step2 = resolve_ward("Whitefield")
    step4 = route_agency(step1, step2)
    assert step4["primary_agency"] in ("BWSSB", "BESCOM")
    assert "BBMP" in step4.get("cc_agencies", [])


def test_uc8_fallen_tree_on_line():
    import agent.llm as llm_mod
    result = llm_mod._mock_route("fallen_tree_on_line")
    assert result["primary_agency"] == "BESCOM"
    assert "BBMP" in result.get("cc_agencies", [])
    result2 = llm_mod._mock_route("fallen_tree")
    assert result2["primary_agency"] == "BBMP"


def test_uc9_traffic_signal():
    step1 = _analyze_with_hint("signal")
    assert step1["issue_type"] == "traffic_signal"
    step2 = resolve_ward("Hebbal")
    step4 = route_agency(step1, step2)
    assert step4["primary_agency"] == "Traffic Police"
    assert step4["helpline"] == "112"


def test_uc10_duplicate():
    locality = "Electronic City"
    step1 = _analyze_with_hint("pothole", "uc10 test")
    step1["issue_type"] = "pothole"
    step2 = resolve_ward(locality)
    step4 = route_agency(step1, step2)
    step5 = draft_complaint(step1, step2, step4)
    step6 = file_and_schedule(step1, step2, step4, step5)
    tid = step6["ticket_id"]
    step3 = find_duplicates("pothole", locality)
    assert step3["duplicate"] is not None
    assert step3["duplicate"]["ticket_id"] == tid
    increment_plus_one(tid)
    updated = get_ticket_by_id(tid)
    assert updated["plus_one_count"] >= 1


def test_uc11_escalation():
    locality = "Yelahanka"
    step1 = _analyze_with_hint("pothole", "uc11 escalation")
    step1["issue_type"] = "pothole_uc11"
    step2 = resolve_ward(locality)
    step4 = route_agency(step1, step2)
    step5 = draft_complaint(step1, step2, step4)
    step6 = file_and_schedule(step1, step2, step4, step5)
    tid = step6["ticket_id"]
    yesterday = (datetime.utcnow() - timedelta(days=1)).strftime("%Y-%m-%d")
    update_ticket(tid, due_date=yesterday)
    traces = run_escalation(datetime.utcnow())
    escalated_ids = [t["ticket_id"] for t in traces]
    assert tid in escalated_ids
    t = next(tr for tr in traces if tr["ticket_id"] == tid)
    assert t["escalation_level"] >= 1
    assert len(t["escalation_message"]) > 20


def test_uc12_unclear_photo():
    import agent.llm as llm_mod
    result = llm_mod._mock_analyze("unclear")
    assert result["is_civic_issue"] is False
    assert result["confidence"] < 0.6


def test_uc13_low_confidence():
    import agent.llm as llm_mod
    result = llm_mod._mock_analyze("unclear")
    assert result["confidence"] < 0.6


def test_uc14_text_only():
    step1 = analyze_photo(None, "There is a large pothole on the main road")
    assert step1["step"] == "analyze_photo"
    assert step1["is_civic_issue"] is True
    assert step1["issue_type"] != ""


def test_full_flow_timing():
    import time
    t0 = time.time()
    step1 = _analyze_with_hint("pothole")
    step2 = resolve_ward("Koramangala")
    find_duplicates(step1["issue_type"] + "_timing", "Koramangala")
    step4 = route_agency(step1, step2)
    step5 = draft_complaint(step1, step2, step4)
    step6 = file_and_schedule(step1, step2, step4, step5)
    elapsed = time.time() - t0
    assert elapsed < 15, f"Full flow took {elapsed:.1f}s, expected < 15s"
    assert step6["ticket_id"].startswith("WF-")


def test_ticket_id_format():
    step1 = _analyze_with_hint("garbage")
    step2 = resolve_ward("Indiranagar")
    step4 = route_agency(step1, step2)
    step5 = draft_complaint(step1, step2, step4)
    step6 = file_and_schedule(step1, step2, step4, step5)
    tid = step6["ticket_id"]
    parts = tid.split("-")
    assert parts[0] == "WF"
    assert len(parts[1]) == 8
    assert parts[2].isdigit()


def test_due_date_severity():
    from db.database import _DUE_DAYS
    assert _DUE_DAYS[5] == 1
    assert _DUE_DAYS[4] == 2
    assert _DUE_DAYS[3] == 5
    assert _DUE_DAYS[1] == 7
    assert _DUE_DAYS[2] == 7
