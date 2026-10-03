# Client release status — 3 October 2026

**Local release verified. Cloud release awaiting account configuration; no live client URL verified.**

## Fixed

- Explicit-range Google Sheets sync, column-name updates and read-back verification.
  Live CRM has **1,005 unique domains**, matching **1,005 local qualified leads**;
  **zero missing domains**. The app's separate Sheet was retained.
- Durable outreach ledger, prior Gmail history checks, suppression, replies, opt-outs,
  bounces, daily caps, two follow-ups and all three signature links. SMTP acceptance
  is distinguished from failed and uncertain submission. Uncertain sends never retry
  automatically. Definite transient failures retain their identity with backoff and
  a maximum of three attempts; permanent rejections stop.
- Local batch launcher starts the complete dashboard and sync/scheduling service.
  Local dashboard health returned HTTP 200; scheduler heartbeat verified.
- Hosted login fails closed, uses a salted PBKDF2 password, throttles attempts, expires
  sessions and offers logout. Full workspace controls are available after login.
- Consistent compressed SQLite backups include committed WAL data. Cloud owners
  use atomic database leases and fenced writes. Outreach checkpoints synchronously;
  a storage failure prevents further sends. Local sending is disabled after bootstrap.
- Actions workflows have explicit opt-in gates; the saved automatic-send toggle is
  independently required. Publishing code alone cannot start outreach.

## Root causes

See [the initial audit](RELEASE_AUDIT.md): inferred Sheet appends and shifted column
indices, incomplete delivery reconciliation, optional hosted authentication, raw
SQLite copying and competing jobs overwriting the same snapshots.

## Files modified

- UI/entrypoints: `app.py`, `streamlit_app.py`, `outreach_worker.py`, `scripts/start.py`.
- CRM/outreach: `core/sheets.py`, `pipeline.py`, `email_sender.py`, `mailbox.py`,
  `outreach_store.py`, `outreach_drafts.py`, `outreach_scheduler.py`, `outreach_ui.py`,
  `local_lock.py`.
- Cloud/auth: `core/auth.py`, `config.py`, `cloud_runtime.py`, `supabase_client.py`,
  `supabase_state.py`; small integration hooks in `store.py` and `campaigns.py`.
- Deployment: `scripts/cloud_cycle.py`, `push_cloud_state.py`, `prepare_release.py`,
  `.github/workflows`, `.gitignore`, `.env.example`, `requirements.txt`, docs.
- Regression coverage: `tests/test_outreach.py`, `test_cloud_release.py`, updated
  Sheet/UI fixtures in `test_campaigns.py` and `test_ui.py`.

## Migrations

Local SQLite adds `attempts`, `retry_at` and `retryable` to existing delivery tables
without deleting data. Supabase needs `supabase/schema.sql`, followed by
`supabase/002_atomic_workspace.sql`. These cloud migrations have **not** been run
against the user's project because the required server key/management connection
is not configured. Existing cloud data is never overwritten by bootstrap.

## Test results

- **74 automated tests passed** using isolated databases and mocked email delivery.
  Includes 1,000-target exact stopping/resume, deduplication, extraction and evidence
  rules, source cooldowns, Sheet preservation and verified writes, caps, suppression,
  reply stops, interrupted-send recovery, concurrency, retries, WAL backup restore,
  stale-owner rejection, password login/logout, missing-auth protection and UI pages.
- Python compilation, `pip check` and `git diff --check` passed.
- Live Sheet read-back: 1,005 rows / 1,005 domains / zero missing qualified leads.
- Gmail SMTP authentication and readonly IMAP checks passed locally. **No real
  debugging email or outreach email was sent.** Delivery ledger remains empty.
- No Node frontend, REST server, lint/type-check configuration or build script is
  present. Python tests, compilation and Streamlit AppTest are the applicable checks.
- Cloud snapshot tests exercise the client and failure handling with fakes. Actual
  PostgreSQL functions, cloud restore and hosted SMTP/IMAP remain integration checks.

## Security

Private `.env`, credentials, databases, backups, generated deployment secrets and
`.streamlit/secrets.toml` are ignored by Git. A source-file secret-pattern scan found
no private keys or token patterns. Supabase secrets remain server-side; SQL enables
RLS and restricts lease/checkpoint functions to the service role. There is one shared
client workspace, not tenant isolation. Provider messages/credentials are not exposed
in user-facing connection errors.

## Remaining / manual configuration

1. Configure **SUPABASE_SERVICE_ROLE_KEY** privately. The project URL is present;
   an anonymous/publishable key is insufficient. Supabase plugin access is optional
   for applying migrations and is not yet connected.
2. Apply both SQL migrations, bootstrap the verified local data once, then regenerate
   the private Streamlit secrets template.
3. In Streamlit Community Cloud, deploy `Alihasnat930/Lead-generation`, `main`,
   **streamlit_app.py**, Python **3.11**, with those secrets.
4. Verify deployed login, restored data, Sheet access and Gmail authentication.
   Share its resulting URL/password only after those checks pass. Real delivery can
   be checked later with explicit authorization; current authorization is auth only.

Exact commands and private-file locations: [deployment guide](DEPLOY_STREAMLIT.md).

## Production readiness

Ready for local use and cloud configuration, **not yet verified live for clients**.
Community Cloud may sleep and free providers impose limits. A 1,000-lead target is
not a guarantee across every niche/region, nor proof of mailbox deliverability.
Discovery can repeat its last 30-second research batch after abrupt cloud shutdown;
deduplication protects saved results. Cloud sending stops on a lost lease or failed
checkpoint and requires a restart/reconciliation. No paid API is required.
