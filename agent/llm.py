"""
agent/llm.py - LLM abstraction for Ward Fixer.

MOCK_MODE=true (default): returns canned JSON chosen by keywords in the
image filename or text: roadcut_overdue, roadcut_nopermit, livewire,
telecom, sewer, stormdrain (plus a few single-owner extras).

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
    # Orphan-hazard families (demo filenames)
    "roadcut_overdue": {"family": "ROAD_CUT", "sub_type": "TRENCH_PIPE", "severity": 3, "confidence": 0.90, "is_civic_issue": True,
                        "description": "Trench about 1.5 m wide cut across the carriageway with a blue pipe exposed; loose soil left on the road and no restoration.",
                        "visual_clues": ["blue_hdpe_pipe", "water_pooling_in_trench", "bwssb_stencil_barricade"]},
    "roadcut_nopermit": {"family": "ROAD_CUT", "sub_type": "TRENCH_DUCT", "severity": 3, "confidence": 0.88, "is_civic_issue": True,
                         "description": "Narrow trench along the road edge with orange duct bundles and coiled thin cable; no barricade or work board.",
                         "visual_clues": ["orange_duct_bundle", "coiled_fibre_cable", "no_barricade_or_board"]},
    "livewire": {"family": "CABLE", "sub_type": "LIVE_POWER", "severity": 5, "confidence": 0.93, "is_civic_issue": True,
                 "description": "Thick insulated cable hanging at head height from a pole close to a transformer.",
                 "visual_clues": ["thick_insulated_cable", "pole_transformer_nearby"]},
    "telecom": {"family": "CABLE", "sub_type": "TELECOM_OFC", "severity": 2, "confidence": 0.90, "is_civic_issue": True,
                "description": "Bundle of thin black cables sagging low between poles, small junction box with an operator tag.",
                "visual_clues": ["thin_black_cable_bundle", "telecom_junction_box"]},
    "streetlight": {"family": "CABLE", "sub_type": "STREETLIGHT", "severity": 3, "confidence": 0.86, "is_civic_issue": True,
                    "description": "Streetlight feeder cable hanging loose from the lamp post junction door.",
                    "visual_clues": ["streetlight_pole_cable"]},
    "cable": {"family": "CABLE", "sub_type": "UNSURE", "severity": 4, "confidence": 0.70, "is_civic_issue": True,
              "description": "Cable hanging low over the footpath; type of cable cannot be confirmed from the photo.",
              "visual_clues": ["thin_black_cable_bundle", "thick_insulated_cable"]},
    "sewer": {"family": "OPEN_DRAIN", "sub_type": "SEWER", "severity": 4, "confidence": 0.89, "is_civic_issue": True,
              "description": "Open round manhole with grey-black water close to the surface.",
              "visual_clues": ["round_manhole_frame", "grey_black_sewage", "sewage_smell"]},
    "stormdrain": {"family": "OPEN_DRAIN", "sub_type": "STORM", "severity": 4, "confidence": 0.90, "is_civic_issue": True,
                   "description": "Broken slab over a roadside channel exposing a deep open drain with rainwater and silt.",
                   "visual_clues": ["broken_rectangular_slab", "roadside_channel", "rainwater_and_silt"]},
    "drain_mixed": {"family": "OPEN_DRAIN", "sub_type": "UNSURE", "severity": 4, "confidence": 0.80, "is_civic_issue": True,
                    "description": "Open roadside channel with a broken slab and grey-black sewage flowing in it.",
                    "visual_clues": ["roadside_channel", "broken_rectangular_slab", "grey_black_sewage", "sewage_smell"]},
    # Single-owner path
    "pothole": {"family": "OTHER", "issue_type": "pothole", "severity": 3, "description": "Large pothole approximately 60 cm wide and 15 cm deep on the carriageway.", "confidence": 0.92, "is_civic_issue": True},
    "garbage": {"family": "OTHER", "issue_type": "garbage", "severity": 2, "description": "Unsegregated garbage pile accumulated near a residential gate, possible black spot.", "confidence": 0.95, "is_civic_issue": True},
    "tree": {"family": "OTHER", "issue_type": "fallen_tree", "severity": 3, "description": "Large tree has fallen across the road, obstructing traffic.", "confidence": 0.93, "is_civic_issue": True},
    "signal": {"family": "OTHER", "issue_type": "traffic_signal", "severity": 3, "description": "Traffic signal not functioning at the intersection.", "confidence": 0.91, "is_civic_issue": True},
    "not_civic": {"family": "OTHER", "issue_type": "unknown", "severity": 1, "description": "Image does not appear to show a civic infrastructure issue.", "confidence": 0.85, "is_civic_issue": False},
    "unclear": {"family": "OTHER", "issue_type": "unclear", "severity": 1, "description": "Image is blurry or unclear; cannot determine the issue.", "confidence": 0.3, "is_civic_issue": False},
}

# Single-owner routes. "City Corporation" is replaced with the ward's corporation by route_agency.
_MOCK_ROUTE: dict[str, dict] = {
    "pothole": {"primary_agency": "City Corporation", "cc_agencies": [], "department": "Roads & Infrastructure", "channel": "corporation helpline 1533", "helpline": "1533", "reason": "Potholes on ward roads are repaired by the city corporation's roads division."},
    "garbage": {"primary_agency": "City Corporation", "cc_agencies": [], "department": "Solid Waste Management", "channel": "corporation helpline 1533", "helpline": "1533", "reason": "Garbage on public roads is cleared by the city corporation's solid waste wing."},
    "fallen_tree": {"primary_agency": "City Corporation", "cc_agencies": [], "department": "Forest Cell", "channel": "corporation helpline 1533", "helpline": "1533", "reason": "Fallen trees on public roads are cleared by the city corporation's forest cell."},
    "traffic_signal": {"primary_agency": "Traffic Police", "cc_agencies": [], "department": "Traffic Signal Maintenance Cell", "channel": "helpline 112", "helpline": "112", "reason": "Traffic signal faults are handled by Bengaluru City Traffic Police."},
}

_PRIORITY = ["not_civic", "unclear", "roadcut_overdue", "roadcut_nopermit", "livewire", "telecom",
             "streetlight", "drain_mixed", "stormdrain", "sewer", "cable",
             "pothole", "garbage", "tree", "signal"]


def _keyword_match(hint: str) -> str:
    hint_lower = hint.lower()
    for key in _PRIORITY:
        if key in hint_lower:
            return key
    return "pothole"


def _mock_analyze(hint: str) -> dict:
    key = _keyword_match(hint)
    result = json.loads(json.dumps(_MOCK_ANALYZE[key]))
    result["_mock_key"] = key
    return result


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
            image.convert("RGB").save(buf, format="JPEG")
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


def ai_enabled() -> bool:
    """True only when mock mode is off and watsonx credentials are configured."""
    return (not MOCK_MODE) and bool(os.environ.get("WATSONX_APIKEY")) and bool(os.environ.get("WATSONX_PROJECT_ID"))
