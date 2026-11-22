"""
app.py - Ward Fixer Streamlit UI

Run: streamlit run app.py
"""
from datetime import datetime
import streamlit as st
from PIL import Image

from db.database import init_db, get_open_tickets, increment_plus_one, mark_resolved
from agent.tools import analyze_photo, resolve_ward, find_duplicates, route_agency, draft_complaint, file_and_schedule
from agent.escalation import run_escalation

init_db()

st.set_page_config(page_title="Ward Fixer", page_icon="🛠️", layout="centered")

_LOCALITIES = ["Indiranagar", "Koramangala", "HSR Layout", "Whitefield", "Jayanagar",
               "Malleshwaram", "Yelahanka", "BTM Layout", "Hebbal", "Electronic City"]

_AGENCY_COLOURS = {"BBMP": "#2563eb", "BESCOM": "#dc2626", "BWSSB": "#16a34a", "Traffic Police": "#7c3aed"}
_SEVERITY_COLOURS = {5: "#dc2626", 4: "#ea580c", 3: "#d97706", 2: "#2563eb", 1: "#6b7280"}
_SIM_LABEL = "\u26a0\ufe0f Simulated filing \u2013 sample ward data"


def _agency_badge(agency):
    colour = _AGENCY_COLOURS.get(agency, "#374151")
    st.markdown(f'<div style="background:{colour};color:#fff;padding:12px 20px;border-radius:8px;font-size:1.3rem;font-weight:700;text-align:center;">{agency}</div>', unsafe_allow_html=True)


def _severity_chip(severity):
    colour = _SEVERITY_COLOURS.get(severity, "#6b7280")
    label = {5: "EMERGENCY", 4: "HIGH", 3: "MEDIUM", 2: "LOW", 1: "MINOR"}.get(severity, str(severity))
    return f'<span style="background:{colour};color:#fff;padding:2px 10px;border-radius:12px;font-size:0.8rem;font-weight:600;">Sev {severity} \u2013 {label}</span>'


def _stepper(traces):
    for i, trace in enumerate(traces, 1):
        step_name = trace.get("step", f"Step {i}").replace("_", " ").title()
        output = trace.get("output", "")
        ms = trace.get("duration_ms", 0)
        with st.expander(f"{i}. {step_name}  \u2014  {output[:80]}", expanded=False):
            st.caption(f"Input: {trace.get('input_summary', '')}")
            st.caption(f"Output: {output}")
            st.caption(f"Duration: {ms} ms")


def tab_report():
    st.info(_SIM_LABEL)
    uploaded = st.file_uploader("Photo of the issue (optional)", type=["jpg", "jpeg", "png", "webp"],
                                help="Leave blank to do a text-only report (UC14)")
    image = None
    if uploaded:
        image = Image.open(uploaded)
        st.image(image, caption="Uploaded photo", use_container_width=True)

    locality = st.selectbox("\ud83d\udccd Locality", _LOCALITIES)
    description = st.text_area("\ud83d� Description (optional)",
                               placeholder="e.g. Large pothole near HDFC ATM, vehicles swerving", height=80)
    if not st.button("\ud83d� Run agent", type="primary", use_container_width=True):
        return

    traces = []
    hint = f"{uploaded.name} {description}" if uploaded else description

    with st.spinner("Analysing\u2026"):
        step1 = analyze_photo(image, description)
        traces.append(step1)

    if not step1.get("is_civic_issue", True):
        st.warning("\ud83d\udeab This does not appear to be a civic issue. Please describe the problem in a sentence.")
        _stepper(traces)
        return

    if float(step1.get("confidence", 1.0)) < 0.6:
        st.warning(f"\u26a0\ufe0f Low confidence ({step1['confidence']:.0%}). Best guess: **{step1['issue_type']}**. Please confirm or update the description and re-run.")
        _stepper(traces)
        return

    with st.spinner("Resolving ward\u2026"):
        step2 = resolve_ward(locality)
        traces.append(step2)

    with st.spinner("Checking duplicates\u2026"):
        step3 = find_duplicates(step1["issue_type"], locality)
        traces.append(step3)

    if step3.get("duplicate"):
        dup = step3["duplicate"]
        increment_plus_one(dup["ticket_id"])
        st.warning(f"\ud83d� **Duplicate detected.** An open ticket already exists for this issue in {locality}.")
        c1, c2 = st.columns(2)
        c1.metric("Ticket ID", dup["ticket_id"])
        c2.metric("Status", dup["status"])
        c1.metric("+1 count", dup["plus_one_count"] + 1)
        c2.metric("Due", dup["due_date"])
        _stepper(traces)
        return

    with st.spinner("Routing to agency\u2026"):
        step4 = route_agency(step1, step2)
        traces.append(step4)

    with st.spinner("Drafting complaint\u2026"):
        step5 = draft_complaint(step1, step2, step4)
        traces.append(step5)

    with st.spinner("Filing ticket\u2026"):
        step6 = file_and_schedule(step1, step2, step4, step5)
        traces.append(step6)

    st.markdown("---")
    st.subheader("\ud83d� Agent steps")
    _stepper(traces)

    st.markdown("---")
    st.subheader("\ud83c� Responsible Agency")
    _agency_badge(step4.get("primary_agency", "BBMP"))
    if step4.get("helpline"):
        st.markdown(f"**Helpline: \ud83d� {step4['helpline']}**")
    if step4.get("cc_agencies"):
        st.caption(f"CC: {', '.join(step4['cc_agencies'])}")
    st.caption(f"Reason: {step4.get('reason', '')}")

    if int(step1.get("severity", 3)) == 5:
        st.error(f"\ud83d� **EMERGENCY \u2013 Severity 5** | Call **{step4.get('helpline')}** immediately! Keep bystanders at least 10 metres away.")

    st.markdown("---")
    st.subheader("\ud83d� Drafted Complaints")
    col_en, col_kn = st.columns(2)
    with col_en:
        st.markdown("**English**")
        st.text_area("Subject (EN)", value=step5.get("subject", ""), height=50, key="subj_en")
        st.text_area("Body (EN)", value=step5.get("body_en", ""), height=250, key="body_en")
    with col_kn:
        st.markdown("**Kannada**")
        st.text_area("Subject (KN)", value=step5.get("subject", ""), height=50, key="subj_kn")
        st.text_area("Body (KN)", value=step5.get("body_kn", ""), height=250, key="body_kn")

    st.markdown("---")
    st.success(f"\u2705 Ticket filed: **{step6.get('ticket_id')}**  |  Due by: **{step6.get('due_date')}**")
    st.caption(_SIM_LABEL)


def tab_tickets():
    st.info(_SIM_LABEL)
    tickets = get_open_tickets()
    if not tickets:
        st.info("No tickets yet. Report an issue in the first tab.")
        return

    st.subheader("\u23e9 Escalation simulator")
    if st.button("Simulate +7 days (run escalation agent)", use_container_width=True):
        from datetime import timedelta
        future = datetime.utcnow() + timedelta(days=7)
        esc_traces = run_escalation(future)
        if not esc_traces:
            st.info("No tickets were overdue in the simulated future.")
        else:
            st.success(f"Escalated {len(esc_traces)} ticket(s).")
            for t in esc_traces:
                with st.expander(f"Ticket {t['ticket_id']} \u2192 Level {t['escalation_level']} ({t['officer']})", expanded=True):
                    st.caption(t["output"])
                    st.text_area("Escalation message", value=t["escalation_message"], height=200, key=f"esc_{t['ticket_id']}")
        st.rerun()

    st.markdown("---")
    st.subheader("\ud83c�\ufe0f All tickets")
    for ticket in get_open_tickets():
        sev = int(ticket.get("severity", 3))
        with st.container(border=True):
            col1, col2 = st.columns([3, 1])
            with col1:
                st.markdown(
                    f"**{ticket.get('ticket_id')}** &nbsp; "
                    + _severity_chip(sev)
                    + f"&nbsp; <span style='font-size:0.85rem'>{ticket.get('issue_type','').replace('_',' ').title()} \u00b7 {ticket.get('locality','')}</span>",
                    unsafe_allow_html=True,
                )
                st.caption(f"Agency: **{ticket.get('primary_agency','')}** \u00b7 Status: **{ticket.get('status','')}** \u00b7 +1: **{ticket.get('plus_one_count',0)}** \u00b7 Due: **{ticket.get('due_date','')}** \u00b7 Escalation: **{ticket.get('escalation_level',0)}**")
                if ticket.get("escalation_msg"):
                    with st.expander("Escalation message"):
                        st.text(ticket["escalation_msg"])
            with col2:
                if ticket.get("status") != "Resolved":
                    if st.button("\u2705 Resolve", key=f"res_{ticket['ticket_id']}"):
                        mark_resolved(ticket["ticket_id"])
                        st.rerun()
                else:
                    st.caption("\u2705 Resolved")
    st.caption(_SIM_LABEL)


tab1, tab2 = st.tabs(["\ud83d� Report an issue", "\ud83c�\ufe0f My tickets"])
with tab1:
    tab_report()
with tab2:
    tab_tickets()
