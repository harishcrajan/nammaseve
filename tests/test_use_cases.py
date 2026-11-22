"""One test per Ward Fixer use case (mock mode, keyed by sample filename)."""
from datetime import date, datetime, timedelta

from PIL import Image

from agent.tools import run_agent, analyze_photo, resolve_ward, attribute_owner, check_permit, route_agency
from agent.escalation import run_escalation
from db.database import get_ticket_by_id, mark_sent

LOC = "Indiranagar"
CORP = "Bengaluru East City Corporation"
ALL_STEPS = ["analyze_photo", "resolve_ward", "find_duplicates", "attribute_owner",
             "route_agency", "draft_complaint", "file_and_schedule"]


def _run(filename, locality=LOC):
    return run_agent(None, locality, filename=filename)


def test_roadcut_overdue_permit():
    r = _run("roadcut_overdue.jpg")
    assert r["outcome"] == "filed"
    assert [t["step"] for t in r["traces"]] == ALL_STEPS
    assert r["attribution"]["verdict"] == "BWSSB"
    assert r["attribution"]["suspects"][0]["confidence"] >= 0.6
    assert r["routing"]["primary_agency"] == "BWSSB"
    assert CORP in r["routing"]["cc_agencies"]


def test_roadcut_no_permit_possible_unauthorised_cut():
    r = _run("roadcut_nopermit.jpg")
    assert r["routing"]["primary_agency"] == CORP
    assert "road-cutting permit" in r["complaint"]["body_en"]
    assert r["complaint"]["body_en"] and r["complaint"]["body_kn"]
    assert get_ticket_by_id(r["filing"]["ticket_id"])["status"] == "Draft ready"


def test_roadcut_permit_not_yet_due_is_watching():
    issue = {"family": "ROAD_CUT", "sub_type": "TRENCH_OTHER", "visual_clues": ["red_warning_tiles"]}
    ward = resolve_ward(LOC)
    attribution = attribute_owner(issue, ward)
    assert attribution["verdict"] == "BESCOM"
    permit = check_permit(issue, ward, attribution, today=date(2026, 11, 22))
    assert permit["permit_status"] == "WATCHING"


def test_attribute_owner_returns_evidence_suspects_verdict():
    r = _run("roadcut_nopermit.jpg")
    a = r["attribution"]
    assert len(a["evidence"]) == 3
    assert [s["confidence"] for s in a["suspects"]] == sorted((s["confidence"] for s in a["suspects"]), reverse=True)
    assert a["verdict"] and a["verdict_basis"]


def test_cable_live_power_bescom_severity5_banner():
    r = _run("livewire.jpg")
    assert r["routing"]["primary_agency"] == "BESCOM"
    assert r["routing"]["severity"] == 5
    assert r["routing"]["safety_banner"] is True


def test_cable_unsure_defaults_to_live_power():
    issue = analyze_photo(None, "some cable hanging")
    assert issue["sub_type"] == "UNSURE"
    routing = route_agency(issue, resolve_ward(LOC))
    assert issue["sub_type"] == "LIVE_POWER"
    assert routing["primary_agency"] == "BESCOM" and routing["safety_banner"]


def test_cable_telecom_ofc():
    r = _run("telecom.jpg")
    assert r["routing"]["primary_agency"] == CORP
    assert "Telecom operator (OFC)" in r["routing"]["cc_agencies"]
    assert not r["routing"]["safety_banner"]


def test_cable_streetlight():
    r = _run("streetlight_cable.jpg")
    assert r["issue"]["sub_type"] == "STREETLIGHT"
    assert r["routing"]["primary_agency"] == CORP


def test_open_drain_sewer_bwssb():
    r = _run("sewer.jpg")
    assert r["routing"]["primary_agency"] == "BWSSB"
    assert r["issue"]["sub_type"] == "SEWER"


def test_open_drain_storm_city_corporation():
    r = _run("stormdrain.jpg")
    assert r["routing"]["primary_agency"] == CORP
    assert r["issue"]["sub_type"] == "STORM"


def test_open_drain_both():
    r = _run("drain_mixed.jpg")
    assert r["attribution"]["verdict"] == "BOTH"
    assert r["routing"]["primary_agency"] == "BWSSB"
    assert CORP in r["routing"]["cc_agencies"]


def test_single_owner_short_path():
    r = _run("pothole.jpg")
    steps = [t["step"] for t in r["traces"]]
    assert "attribute_owner" not in steps and "check_permit" not in steps
    assert r["routing"]["primary_agency"] == CORP


def test_duplicate_adds_plus_one():
    first = _run("sewer.jpg")
    second = _run("sewer.jpg")
    assert second["outcome"] == "duplicate"
    assert second["duplicate"]["ticket_id"] == first["filing"]["ticket_id"]
    assert get_ticket_by_id(first["filing"]["ticket_id"])["plus_one_count"] == 1


def test_escalation_after_seven_days():
    r = _run("roadcut_nopermit.jpg")
    mark_sent(r["filing"]["ticket_id"])
    traces = run_escalation(datetime.utcnow() + timedelta(days=7))
    assert len(traces) == 1 and traces[0]["escalation_level"] == 1
    assert r["filing"]["ticket_id"] in traces[0]["escalation_message"]
    assert get_ticket_by_id(r["filing"]["ticket_id"])["escalation_level"] == 1


def test_wards_use_gba_corporations():
    corps = {resolve_ward(l)["corporation"] for l in
             ["Indiranagar", "Koramangala", "Malleshwaram", "Hebbal", "Shivajinagar"]}
    assert corps == {f"Bengaluru {z} City Corporation" for z in ["Central", "East", "West", "North", "South"]}


def test_uploaded_photo_uses_filename_keyword():
    img = Image.new("RGB", (10, 10))
    r = run_agent(img, LOC, filename="livewire.jpg")
    assert r["issue"]["sub_type"] == "LIVE_POWER"


def test_guided_answers_live_power_and_drain_both():
    from agent.tools import run_guided
    r = run_guided({"hazard": "Cable or wire", "where": "lying on footpath", "live": "not sure"}, "Koramangala")
    assert r["routing"]["primary_agency"] == "BESCOM" and r["routing"]["safety_banner"]
    r = run_guided({"hazard": "Open drain or manhole", "shape": "rectangular slab or roadside channel", "smell": "yes"}, "Hebbal")
    assert r["attribution"]["verdict"] == "BOTH"
