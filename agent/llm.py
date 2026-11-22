"""
agent/llm.py - LLM abstraction for Ward Fixer.

MOCK_MODE=true (default): returns realistic canned JSON keyed on
keywords found in image filename or text.

MOCK_MODE=false: calls IBM watsonx.ai via ibm-watsonx-ai SDK.
  Required env vars: WATSONX_APIKEY, WATSONX_PROJECT_ID, WATSONX_URL,
                     VISION_MODEL_ID, TEXT_MODEL_ID
"""
import os
import json
import base64
import io
import re

from dotenv import load_dotenv
load_dotenv()

MOCK_MODE = os.environ.get("MOCK_MODE", "true").lower() not in ("false", "0", "no")

_MOCK_ANALYZE: dict[str, dict] = {
    "pothole": {"issue_type": "pothole", "severity": 3, "description": "Large pothole approximately 60 cm wide and 15 cm deep on the carriageway.", "confidence": 0.92, "is_civic_issue": True},
    "damaged_road": {"issue_type": "damaged_road", "severity": 3, "description": "Road surface severely damaged with multiple cracks and depressions.", "confidence": 0.88, "is_civic_issue": True},
    "garbage": {"issue_type": "garbage", "severity": 2, "description": "Unsegregated garbage pile accumulated near a residential gate, possible black spot.", "confidence": 0.95, "is_civic_issue": True},
    "waste": {"issue_type": "garbage", "severity": 2, "description": "Waste dumped on public road, creating a sanitation hazard.", "confidence": 0.90, "is_civic_issue": True},
    "streetlight": {"issue_type": "streetlight", "severity": 2, "description": "Streetlight column present but not illuminated; surrounding area dark at night.", "confidence": 0.91, "is_civic_issue": True},
    "light": {"issue_type": "streetlight", "severity": 2, "description": "Street light not functioning.", "confidence": 0.87, "is_civic_issue": True},
    "wire": {"issue_type": "dangling_wire", "severity": 5, "description": "Snapped power line hanging low over the road, posing immediate electrocution risk.", "confidence": 0.97, "is_civic_issue": True},
    "power": {"issue_type": "dangling_wire", "severity": 5, "description": "Live electrical wire dangling at head height - emergency hazard.", "confidence": 0.94, "is_civic_issue": True},
    "sparking": {"issue_type": "transformer", "severity": 5, "description": "Transformer sparking and emitting smoke near a residential colony.", "confidence": 0.96, "is_civic_issue": True},
    "transformer": {"issue_type": "transformer", "severity": 5, "description": "Overloaded transformer showing signs of failure - risk of fire.", "confidence": 0.93, "is_civic_issue": True},
    "leak": {"issue_type": "water_leak", "severity": 3, "description": "Continuous water leak from an underground pipe visible at road surface.", "confidence": 0.90, "is_civic_issue": True},
    "pipe": {"issue_type": "water_leak", "severity": 3, "description": "Broken water pipe causing flooding on the footpath.", "confidence": 0.88, "is_civic_issue": True},
    "burst": {"issue_type": "water_burst", "severity": 4, "description": "Water main burst - high-pressure water flooding the road.", "confidence": 0.94, "is_civic_issue": True},
    "manhole": {"issue_type": "sewage_overflow", "severity": 4, "description": "Open manhole with sewage overflow creating a public health hazard.", "confidence": 0.89, "is_civic_issue": True},
    "sewage": {"issue_type": "sewage_overflow", "severity": 4, "description": "Sewage overflowing from drain onto road surface.", "confidence": 0.92, "is_civic_issue": True},
    "tree": {"issue_type": "fallen_tree", "severity": 3, "description": "Large tree has fallen across the road, obstructing traffic.", "confidence": 0.93, "is_civic_issue": True},
    "branch": {"issue_type": "fallen_tree", "severity": 3, "description": "Heavy tree branch fallen on the footpath.", "confidence": 0.88, "is_civic_issue": True},
    "signal": {"issue_type": "traffic_signal", "severity": 3, "description": "Traffic signal not functioning at the intersection.", "confidence": 0.91, "is_civic_issue": True},
    "traffic": {"issue_type": "traffic_signal", "severity": 3, "description": "Traffic light out of order, causing congestion.", "confidence": 0.87, "is_civic_issue": True},
    "blocking": {"issue_type": "road_blocking", "severity": 3, "description": "Vehicles illegally parked blocking the road.", "confidence": 0.85, "is_civic_issue": True},
    "dug": {"issue_type": "road_dug_bwssb", "severity": 3, "description": "Road dug up for pipe-laying work and left unrestored.", "confidence": 0.87, "is_civic_issue": True},
    "not_civic": {"issue_type": "unknown", "severity": 1, "description": "Image does not appear to show a civic infrastructure issue.", "confidence": 0.85, "is_civic_issue": False},
    "unclear": {"issue_type": "unclear", "severity": 1, "description": "Image is blurry or unclear; cannot determine the issue.", "confidence": 0.3, "is_civic_issue": False},
}

_MOCK_ROUTE: dict[str, dict] = {
    "pothole": {"primary_agency": "BBMP", "cc_agencies": [], "department": "Roads & Infrastructure Department", "channel": "bbmp.gov.in / helpline 1533", "helpline": "1533", "reason": "Potholes and road surface damage are the responsibility of BBMP Roads & Infrastructure Department."},
    "damaged_road": {"primary_agency": "BBMP", "cc_agencies": [], "department": "Roads & Infrastructure Department", "channel": "bbmp.gov.in / helpline 1533", "helpline": "1533", "reason": "Road surface damage falls under BBMP Roads & Infrastructure Department."},
    "garbage": {"primary_agency": "BBMP", "cc_agencies": [], "department": "Solid Waste Management Department", "channel": "bbmp.gov.in / helpline 1533", "helpline": "1533", "reason": "Garbage collection and disposal on public roads is managed by BBMP Solid Waste Management."},
    "streetlight": {"primary_agency": "BBMP", "cc_agencies": [], "department": "Electrical Division", "channel": "bbmp.gov.in / helpline 1533", "helpline": "1533", "reason": "Street lighting infrastructure within ward limits is maintained by BBMP Electrical Division."},
    "dangling_wire": {"primary_agency": "BESCOM", "cc_agencies": [], "department": "Field Operations - Distribution", "channel": "bescom.org / helpline 1912", "helpline": "1912", "reason": "Snapped or dangling power lines are an immediate BESCOM emergency requiring urgent field response."},
    "transformer": {"primary_agency": "BESCOM", "cc_agencies": [], "department": "Maintenance & Operations Division", "channel": "bescom.org / helpline 1912", "helpline": "1912", "reason": "Transformer faults are managed by BESCOM Maintenance & Operations Division."},
    "water_leak": {"primary_agency": "BWSSB", "cc_agencies": [], "department": "Water Distribution Division", "channel": "bwssb.gov.in / helpline 1916", "helpline": "1916", "reason": "Water pipe leaks on public roads are the responsibility of BWSSB Water Distribution Division."},
    "water_burst": {"primary_agency": "BWSSB", "cc_agencies": [], "department": "Water Distribution Division", "channel": "bwssb.gov.in / helpline 1916", "helpline": "1916", "reason": "Burst water mains are an urgent BWSSB Water Distribution emergency."},
    "sewage_overflow": {"primary_agency": "BWSSB", "cc_agencies": [], "department": "Sewerage Operations Division", "channel": "bwssb.gov.in / helpline 1916", "helpline": "1916", "reason": "Sewage overflow indicates a blocked or damaged sanitary sewer, which BWSSB Sewerage Operations must address."},
    "open_manhole_sewer": {"primary_agency": "BWSSB", "cc_agencies": [], "department": "Sewerage Operations Division", "channel": "bwssb.gov.in / helpline 1916", "helpline": "1916", "reason": "An open manhole on a sanitary sewer line is BWSSB's responsibility as the sewer operator."},
    "open_manhole_stormwater": {"primary_agency": "BBMP", "cc_agencies": [], "department": "Storm Water Drain (SWD) Division", "channel": "bbmp.gov.in / helpline 1533", "helpline": "1533", "reason": "An open manhole on a stormwater drain channel is managed by BBMP Storm Water Drain Division."},
    "fallen_tree": {"primary_agency": "BBMP", "cc_agencies": [], "department": "Forest Cell / Horticulture Department", "channel": "bbmp.gov.in / helpline 1533", "helpline": "1533", "reason": "Fallen trees on public roads are cleared by BBMP Forest Cell / Horticulture Department."},
    "fallen_tree_on_line": {"primary_agency": "BESCOM", "cc_agencies": ["BBMP"], "department": "Field Operations - Distribution", "channel": "bescom.org / helpline 1912", "helpline": "1912", "reason": "Tree on a live power line is a BESCOM electrical safety emergency; BBMP Forest Cell is CC'd for tree removal after the line is made safe."},
    "road_dug_bwssb": {"primary_agency": "BWSSB", "cc_agencies": ["BBMP"], "department": "Water Distribution Division", "channel": "bwssb.gov.in / helpline 1916", "helpline": "1916", "reason": "Road dug for BWSSB pipe work must be restored by BWSSB; BBMP Roads is CC'd for road restoration oversight."},
    "road_dug_bescom": {"primary_agency": "BESCOM", "cc_agencies": ["BBMP"], "department": "Field Operations - Distribution", "channel": "bescom.org / helpline 1912", "helpline": "1912", "reason": "Road dug for BESCOM cable work must be restored by BESCOM; BBMP Roads is CC'd for road restoration oversight."},
    "traffic_signal": {"primary_agency": "Traffic Police", "cc_agencies": [], "department": "Traffic Signal Maintenance Cell", "channel": "TSCOP app / helpline 112", "helpline": "112", "reason": "Traffic signal maintenance and malfunction falls under Bengaluru City Traffic Police."},
    "road_blocking": {"primary_agency": "Traffic Police", "cc_agencies": [], "department": "Traffic Control Room", "channel": "TSCOP app / helpline 112", "helpline": "112", "reason": "Vehicles or objects blocking roads are managed by Bengaluru Traffic Police Enforcement Division."},
}


def _keyword_match(hint: str) -> str:
    hint_lower = hint.lower()
    priority = ["not_civic", "unclear", "sparking", "transformer", "burst", "dug",
                "blocking", "branch", "manhole", "sewage", "pothole", "damaged_road",
                "garbage", "waste", "streetlight", "light", "wire", "power",
                "leak", "pipe", "tree", "signal", "traffic"]
    for key in priority:
        if key in hint_lower:
            return key
    return "pothole"


def _mock_analyze(hint: str) -> dict:
    key = _keyword_match(hint)
    return dict(_MOCK_ANALYZE.get(key, _MOCK_ANALYZE["pothole"]))


def _mock_route(issue_type: str) -> dict:
    return dict(_MOCK_ROUTE.get(issue_type, _MOCK_ROUTE["pothole"]))


def vision_json(image, prompt: str, hint: str = "") -> dict:
    """Analyse an image and return structured JSON."""
    if MOCK_MODE:
        return _mock_analyze(hint)
    try:
        from ibm_watsonx_ai import Credentials
        from ibm_watsonx_ai.foundation_models import ModelInference
        api_key = os.environ["WATSONX_APIKEY"]
        project_id = os.environ["WATSONX_PROJECT_ID"]
        url = os.environ.get("WATSONX_URL", "https://us-south.ml.cloud.ibm.com")
        model_id = os.environ.get("VISION_MODEL_ID", "ibm/granite-3-2-8b-instruct")
        credentials = Credentials(url=url, api_key=api_key)
        model = ModelInference(model_id=model_id, credentials=credentials, project_id=project_id)
        if image is not None:
            buf = io.BytesIO()
            image.save(buf, format="JPEG")
            b64 = base64.b64encode(buf.getvalue()).decode()
            data_url = f"data:image/jpeg;base64,{b64}"
            messages = [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": data_url}}, {"type": "text", "text": prompt}]}]
        else:
            messages = [{"role": "user", "content": prompt}]
        response = model.chat(messages=messages)
        raw = response["choices"][0]["message"]["content"]
        return _parse_json(raw)
    except Exception as exc:
        result = _mock_analyze(hint)
        result["_fallback"] = f"fallback used: {exc}"
        return result


def text_json(prompt: str) -> dict:
    """Call the text LLM with a prompt that expects JSON back."""
    if MOCK_MODE:
        return _mock_route(_extract_issue_type(prompt))
    try:
        from ibm_watsonx_ai import Credentials
        from ibm_watsonx_ai.foundation_models import ModelInference
        api_key = os.environ["WATSONX_APIKEY"]
        project_id = os.environ["WATSONX_PROJECT_ID"]
        url = os.environ.get("WATSONX_URL", "https://us-south.ml.cloud.ibm.com")
        model_id = os.environ.get("TEXT_MODEL_ID", "ibm/granite-3-2-8b-instruct")
        credentials = Credentials(url=url, api_key=api_key)
        model = ModelInference(model_id=model_id, credentials=credentials, project_id=project_id)
        messages = [{"role": "user", "content": prompt}]
        response = model.chat(messages=messages)
        raw = response["choices"][0]["message"]["content"]
        return _parse_json(raw)
    except Exception as exc:
        result = _mock_route(_extract_issue_type(prompt))
        result["_fallback"] = f"fallback used: {exc}"
        return result


def _parse_json(text: str) -> dict:
    try:
        return json.loads(text.strip())
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass
    return {}


def _extract_issue_type(prompt: str) -> str:
    m = re.search(r'"issue_type":\s*"([^"]+)"', prompt)
    if m:
        return m.group(1)
    return _keyword_match(prompt)
