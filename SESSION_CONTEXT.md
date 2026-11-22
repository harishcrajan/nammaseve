# Session Context – Ward Fixer / NammaSeve

> Generated: 2026-11-22  
> Project: **nammaseve** (Ward Fixer civic-complaint agent)

## What Was Built

Ward Fixer is an autonomous civic-complaint agent for Bengaluru residents.  
A resident uploads one photo + locality → the agent identifies the issue, routes it to the correct agency, drafts a formal complaint in English and Kannada, files a tracked ticket, and auto-escalates overdue tickets.

## Tech Stack

| Component | Choice |
|-----------|--------|
| Language | Python |
| UI | Streamlit |
| DB | SQLite (file: `ward_fixer.db`) |
| LLM | IBM watsonx.ai (`ibm-watsonx-ai` SDK) |
| Mock mode | `MOCK_MODE=true` (default) – no credentials needed |

## Use Cases

| UC | Scenario | Agency |
|----|----------|--------|
| UC1 | Pothole / damaged road | BBMP – Roads |
| UC2 | Garbage / black spot | BBMP – Solid Waste |
| UC3 | Streetlight out | BBMP – Electrical |
| UC4 | Dangling wire / sparking transformer | BESCOM, severity 5 |
| UC5 | Water pipe leak / burst | BWSSB |
| UC6 | Sewage overflow / open manhole | BWSSB (sewer) or BBMP (stormwater) |
| UC7 | Road dug up, not restored | BWSSB or BESCOM primary, BBMP cc'd |
| UC8 | Fallen tree | BBMP; if on power line → BESCOM primary |
| UC9 | Traffic signal fault | Traffic Police |
| UC10 | Duplicate issue | +1 to existing ticket |
| UC11 | Overdue ticket | Escalation agent |
| UC12 | Unclear / non-civic photo | Ask for description |
| UC13 | Low confidence (<0.6) | Ask user to confirm |
| UC14 | Text-only report | Classify from description |

## Agencies & Helplines

| Agency | Helpline |
|--------|---------|
| BBMP | 1533 |
| BESCOM | 1912 |
| BWSSB | 1916 |
| Traffic Police | 112 |

## How to Run

```bash
pip install -r requirements.txt
cp .env.example .env   # MOCK_MODE=true for demo
streamlit run app.py
pytest tests/ -v
```

## GitHub

- Repo: https://github.com/harishcrajan/nammaseve
- Owner: harishcrajan
- MCP server: GitHub MCP configured at `C:\Users\91988\.bob\settings\mcp.json`
