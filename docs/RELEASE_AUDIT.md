# Client release audit

Target: Streamlit Community Cloud, one private shared workspace with full client controls.

## Architecture

`app.py` (Streamlit UI) calls `core/campaigns.py` and a Python worker for discovery.
Free search/OpenStreetMap results are enriched from business websites, scored locally,
and checkpointed in SQLite (`core/store.py`). Google Sheets is the separate CRM.
`core/pipeline.py` coordinates Gmail SMTP/IMAP with an SQLite delivery ledger,
suppression, daily limits, replies and follow-ups. There is no REST API, OAuth token
store, Node build or ORM. Optional cloud state uses Supabase and GitHub Actions.

## Initial findings

| Severity | Files | Problem and root cause | Required repair |
|---|---|---|---|
| Critical | core/sheets.py | Leads not visible: table-inferred appends and large blank ranges; hard-coded column numbers shifted after an extra header | Explicit ranges, actual header mapping, read-back verification |
| Critical | core/pipeline.py, core/outreach_store.py | Failed CRM updates or interrupted SMTP could lose evidence of a send | Durable reservations, provider-accepted status, ambiguous-send reconciliation; never retry uncertain sends |
| Critical | core/supabase_state.py, scripts/cloud_cycle.py | Raw SQLite copies omit WAL; both jobs upload both databases; non-atomic leases | Consistent SQLite backup, exclusive workspace lease, fenced writes and send checkpoints |
| Critical | app.py, .gitignore | Missing password silently exposes app; Streamlit secret file not ignored | Fail-closed hosted entrypoint and ignore secret files |
| High | core/outreach_store.py | Failed deliveries recreated without retry count or delay | Retain identity and attempt history; bounded transient retries |
| High | core/pipeline.py | CRM/reply status could change during network calls | Refresh eligibility, suppression, mailbox and caps before submission |
| High | core/outreach_scheduler.py | Manual enable flag could activate scheduled sends; unexpected exceptions stop service | Separate automatic-send opt-in and resilient service loop |
| High | scripts/cloud_cycle.py, scripts/push_cloud_state.py | Script imports depend on working directory | Resolve project root before importing core |
| High | deployment | No configured Supabase, dashboard password or authenticated GitHub CLI in this environment | Prepare release and private setup; verify accounts before claiming live |

## Verified live repairs

The app's existing CRM contains 1,005 qualified business domains after explicit-range
write and read-back verification. This is the app's separate Sheet. Gmail SMTP login
and IMAP authentication passed; no real test or outreach email was sent. Published
emails remain unverified for mailbox deliverability. The completed 1,000-lead campaign
produced US/UK results; it did not establish 1,000 deliverable buyers in every niche.

Final test results and deployment status are recorded in RELEASE_STATUS.md.
