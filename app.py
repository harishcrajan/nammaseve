"""
app.py - NammaSeve (Streamlit UI)

Run: streamlit run app.py
"""
import json
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import quote

import streamlit as st
import streamlit.components.v1 as components
from PIL import Image

from db.database import init_db, get_open_tickets, mark_sent
from agent.tools import (run_guided, prefill_from_photo, HAZARDS, TRENCH_OPTIONS, CABLE_WHERE,
                         CABLE_LIVE, DRAIN_SHAPE, DRAIN_SMELL)
from agent.escalation import run_escalation
from agent.llm import ai_enabled

init_db()

st.set_page_config(page_title="NammaSeve", page_icon="🛠️", layout="centered")

_DATA = Path(__file__).parent / "data"
_CONTACTS = json.loads((_DATA / "contacts.json").read_text(encoding="utf-8"))
_LOCALITIES = json.loads((_DATA / "localities.json").read_text(encoding="utf-8"))["localities"] + ["Other"]
_ACCENT = "#0f766e"
_BANNER = "NammaSeve drafts and routes complaints. You send them."

st.markdown(f"""
<style>
.block-container {{padding-top: 1.2rem; padding-bottom: 2rem; max-width: 880px;}}
header[data-testid="stHeader"], [data-testid="stToolbar"], [data-testid="stDecoration"], footer {{display: none !important;}}
div[data-testid="stVerticalBlock"] {{gap: 0.6rem;}}
h1 {{font-size: 2rem; margin-bottom: 0;}}
h3 {{margin-top: 0; padding-top: 0;}}
.stApp {{background: #f6f7f9;}}
div[data-testid="stVerticalBlockBorderWrapper"] {{background: #fff; border-radius: 12px;}}
@media (max-width: 640px) {{
  .block-container {{padding: 1rem 0.75rem 3rem 0.75rem;}}
  h1 {{font-size: 1.6rem !important;}}
  h3 {{font-size: 1.15rem !important;}}
}}
.ns-badge {{background:{_ACCENT};color:#fff;padding:12px 16px;border-radius:8px;font-size:1.2rem;font-weight:700;text-align:center;word-wrap:break-word;}}
.ns-num {{display:inline-block;background:{_ACCENT};color:#fff;border-radius:50%;width:1.7rem;height:1.7rem;text-align:center;line-height:1.7rem;margin-right:.5rem;font-weight:700;}}
.ns-danger {{background:#b91c1c;color:#fff;padding:14px 16px;border-radius:8px;font-size:1.15rem;font-weight:700;text-align:center;}}
.ns-danger a {{color:#fff;text-decoration:underline;}}
</style>
""", unsafe_allow_html=True)


def _section(num, title):
    st.markdown(f'<h3 style="font-size:1.25rem;margin:0;padding:0;"><span class="ns-num">{num}</span>{title}</h3>',
                unsafe_allow_html=True)


def _contact_for(agency):
    if "BESCOM" in agency:
        return _CONTACTS["BESCOM"]
    if "BWSSB" in agency:
        return _CONTACTS["BWSSB"]
    return _CONTACTS["CITY_CORPORATION"]


def _mailto(email, subject, body):
    return f"mailto:{email}?subject={quote(subject)}&body={quote(body)}"


def _copy_button(text, key):
    components.html(f"""
<button id="b" style="width:100%;padding:8px;border:1px solid {_ACCENT};color:{_ACCENT};background:#fff;border-radius:8px;font-weight:600;cursor:pointer;">Copy</button>
<script>
const t = {json.dumps(text)};
document.getElementById("b").onclick = () => navigator.clipboard.writeText(t).then(() => {{
  document.getElementById("b").innerText = "Copied";
}});
</script>""", height=48)


def _send_block(email, subject, body, key):
    st.code(subject, language=None, wrap_lines=True)
    st.code(body, language=None, wrap_lines=True)
    c1, c2 = st.columns(2)
    with c1:
        st.link_button("Open in email app", _mailto(email, subject, body), type="primary", width="stretch")
    with c2:
        _copy_button(f"{subject}\n\n{body}", key)
    st.caption("Attach your photo before sending.")


def section_describe():
    with st.container(border=True):
        _section(1, "Describe")
        uploaded = st.file_uploader("Photo of the hazard (optional)", type=["jpg", "jpeg", "png", "webp"])
        image = Image.open(uploaded) if uploaded else None
        if image is not None:
            st.image(image, width="stretch")

        prefill = st.session_state.get("prefill", {})
        if ai_enabled():
            if image is not None and st.button("Analyse photo", width="stretch"):
                st.session_state["prefill"] = prefill = prefill_from_photo(image)
                st.rerun()
            if prefill:
                st.caption("Answers pre-filled from your photo. Correct them if needed.")
        else:
            st.info("AI photo analysis is off. Answer 2 quick questions instead.")

        def idx(options, key):
            return options.index(prefill[key]) if prefill.get(key) in options else 0

        col_h, col_l = st.columns(2)
        with col_h:
            answers = {"hazard": st.radio("Hazard type", HAZARDS, index=idx(HAZARDS, "hazard"))}
        with col_l:
            locality = st.selectbox("Locality", _LOCALITIES, index=None, placeholder="Type to search…")
            if locality == "Other":
                locality = st.text_input("Your locality").strip()
        landmark = st.text_input("Road or landmark", placeholder="e.g. 12th Main, near the bus stop").strip()

        q1, q2 = st.columns(2)
        if answers["hazard"] == HAZARDS[0]:
            opts = list(TRENCH_OPTIONS)
            with q1:
                answers["trench"] = st.radio("What can you see in the trench?", opts, index=idx(opts, "trench"))
        elif answers["hazard"] == HAZARDS[1]:
            opts = list(CABLE_WHERE)
            with q1:
                answers["where"] = st.radio("Where is it?", opts, index=idx(opts, "where"))
            opts = list(CABLE_LIVE)
            with q2:
                answers["live"] = st.radio("Any sparking, burning smell or is it thick and insulated?", opts,
                                           index=idx(opts, "live"))
        else:
            opts = list(DRAIN_SHAPE)
            with q1:
                answers["shape"] = st.radio("What does it look like?", opts, index=idx(opts, "shape"))
            opts = list(DRAIN_SMELL)
            with q2:
                answers["smell"] = st.radio("Sewage smell or flow?", opts, index=idx(opts, "smell"))

        if st.button("Find who owns it", type="primary", width="stretch", disabled=not locality):
            st.session_state["last_result"] = run_guided(answers, locality, landmark)
            st.rerun()


def section_owner(res):
    with st.container(border=True):
        _section(2, "Who owns it")
        if res["outcome"] == "duplicate":
            dup = res["duplicate"]
            st.warning(f"You already drafted this hazard here. Added +1 to NammaSeve reference "
                       f"**{dup['ticket_id']}** (now {dup['plus_one_count']}).")
            return
        routing, attribution = res["routing"], res.get("attribution", {})
        st.markdown(f'<div class="ns-badge">{routing["primary_agency"]}</div>', unsafe_allow_html=True)
        if routing.get("cc_agencies"):
            st.caption(f"Copy to: {', '.join(routing['cc_agencies'])}")
        st.markdown("**Because you reported:** " + "; ".join(res["issue"].get("reported", [])) + ".")
        if attribution.get("evidence"):
            st.markdown("**Which points to:**")
            for e in attribution["evidence"]:
                st.markdown(f"- {e['label']} → _{', '.join(e['points_to'])}_")
        for s in attribution.get("suspects", []):
            st.progress(float(s["confidence"]), text=f"{s['agency']} - {s['confidence']:.0%}")
        st.markdown(f"**Why:** {routing.get('reason', '')}")
        with st.expander("Agent trace"):
            for i, t in enumerate(res["traces"], 1):
                st.caption(f"{i}. {t['step']} - {t.get('output', '')}")


def section_send(res):
    if res["outcome"] != "filed":
        return
    routing, complaint, filing = res["routing"], res["complaint"], res["filing"]
    contact = _contact_for(routing["primary_agency"])
    with st.container(border=True):
        _section(3, "Send")
        st.markdown("**Ready to send**")
        st.markdown(f"**To:** {contact['email']}  \n**Helpline:** [{contact['helpline']}](tel:{contact['helpline']})"
                    f"  \n**WhatsApp:** [{contact['whatsapp']}](https://wa.me/91{contact['whatsapp']})")
        st.caption("Published contact. Verify before sending.")
        tab_en, tab_kn = st.tabs(["English", "ಕನ್ನಡ"])
        with tab_en:
            _send_block(contact["email"], complaint["subject"], complaint["body_en"], "en")
        with tab_kn:
            _send_block(contact["email"], complaint["subject_kn"], complaint["body_kn"], "kn")
        st.caption(f"NammaSeve reference: {filing['ticket_id']} (local only)")


def section_complaints():
    tickets = get_open_tickets()
    if not tickets:
        return
    st.subheader("Your complaints")
    for t in tickets:
        with st.container(border=True):
            st.markdown(f"**{t.get('subject') or t['issue_type']}**")
            st.caption(f"NammaSeve reference {t['ticket_id']} (local only) · To: {t['primary_agency']}"
                       f" · Status: **{t['status']}** · +1: {t.get('plus_one_count', 0)}")
            if t["status"] == "Draft ready":
                if st.button("I have sent it", key=f"sent_{t['ticket_id']}"):
                    mark_sent(t["ticket_id"])
                    st.rerun()

    with st.container(border=True):
        st.markdown("#### Follow-up")
        if st.button("Draft follow-up (demo: skip 7 days)", width="stretch"):
            st.session_state["followups"] = run_escalation(datetime.now() + timedelta(days=7))
            st.rerun()
        followups = st.session_state.get("followups")
        if followups is not None:
            if not followups:
                st.info("No complaints marked \"Sent by you\" yet. Press \"I have sent it\" after sending.")
            for f in followups:
                ticket = next((t for t in tickets if t["ticket_id"] == f["ticket_id"]), {})
                email = _contact_for(ticket.get("primary_agency", ""))["email"]
                st.markdown(f"**{f['ticket_id']}** - follow-up to {f['officer']} (sent {f['sent_on']})")
                st.code(f["subject"], language=None, wrap_lines=True)
                st.code(f["escalation_message"], language=None, wrap_lines=True)
                st.link_button("Open in email app", _mailto(email, f["subject"], f["escalation_message"]),
                               key=f"fu_{f['ticket_id']}")


res = st.session_state.get("last_result")
if res and res["outcome"] == "filed" and res["routing"].get("safety_banner"):
    st.markdown('<div class="ns-danger">Keep away. Call BESCOM 1912 now. '
                '<a href="tel:1912">Tap to call 1912</a></div>', unsafe_allow_html=True)

st.title("NammaSeve")
st.caption("Find who owns a civic hazard in Bengaluru, and send them a complaint that sticks.")
st.info(_BANNER)

section_describe()
res = st.session_state.get("last_result")
if res:
    section_owner(res)
    section_send(res)
section_complaints()
