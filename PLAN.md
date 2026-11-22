# Ward Fixer – Build Plan

## File Tree

```
nammaseve/
├── app.py                      # Streamlit entrypoint
├── .env.example
├── .gitignore
├── PLAN.md
├── README.md
├── SESSION_CONTEXT.md
├── requirements.txt
├── data/
│   ├── wards.json              # 10 Bengaluru localities (sample data)
│   └── routing_rules.json      # Agency routing rules + ambiguous cases
├── agent/
│   ├── __init__.py
│   ├── llm.py                  # vision_json(), text_json()
│   ├── tools.py                # 6 pipeline steps
│   └── escalation.py           # run_escalation(now)
├── db/
│   ├── __init__.py
│   └── database.py             # SQLite helpers
├── tests/
│   └── test_use_cases.py       # UC1–UC14
└── scripts/
    └── smoke_test.py           # Real watsonx.ai smoke test
```

## Agent Pipeline

| Step | Function | Returns |
|------|----------|---------|
| 1 | `analyze_photo(image, text)` | `issue_type, severity, confidence, is_civic_issue` |
| 2 | `resolve_ward(locality)` | `ward, zone, corporation` |
| 3 | `find_duplicates(issue_type, locality)` | existing ticket or None |
| 4 | `route_agency(issue, ward)` | `primary_agency, cc_agencies, department, helpline, reason` |
| 5 | `draft_complaint(issue, ward, routing)` | `subject, body_en, body_kn` |
| 6 | `file_and_schedule(...)` | `ticket_id (WF-YYYYMMDD-NNN), due_date` |

## Severity → Due Date

| Severity | Due (days) |
|----------|----------|
| 5 | 1 |
| 4 | 2 |
| 3 | 5 |
| 1–2 | 7 |

## Escalation Levels

| Level | Target |
|-------|--------|
| 0 | Ward Engineer |
| 1 | Zonal Commissioner |
| 2 | Public social post draft |
