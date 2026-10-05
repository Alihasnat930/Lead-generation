# Client release status — 6 October 2026

**Live administrator login and the Social research page are verified.**

App: https://lead-generation-2dxsvsggteqbzggxfiu8qo.streamlit.app/
The hosted health endpoint returns `200 ok`. Private secrets are loaded, and a
normal administrator login successfully opened the Social research form.
The deployed entrypoint is `streamlit_app.py` on `main`; the host reports Python
3.14 and Streamlit 1.65.0. Automated release tests ran on Python 3.11. The app was
resumed from shutdown during verification. No campaign or message was triggered.

The new [Social research module](SOCIAL_RESEARCH.md) provides academic-support
topics, UK/US evidence, indexed Reddit/Facebook post discovery, configured Discord
bot channels, imports, review and CSV export. These records stay separate from the
business CRM and outbound queues. Facebook's live probe was unavailable; Discord
still needs bot credentials and channel IDs. The target is research posts, not
verified student customers.

Latest update: GitHub CLI is authenticated as `Alihasnat930`. Account login/signup,
approved email/Google access, Twilio and Meta WhatsApp settings, a consent-aware
template queue and a dedicated Schedule page are implemented. The admin is
`alihasnat.dev@gmail.com`. Supabase Auth is reachable with email confirmation enabled.
Supabase CLI is authenticated, both SQL migrations are applied, and both cloud
snapshots have been restored and checked. GitHub Actions secrets are configured;
both automatic workflow gates remain disabled.
Supabase Auth redirects point to the live URL. Custom signup SMTP uses the existing
Gmail account with email confirmation required; configuration was read back, but no
verification message was sent. Private deployment secrets include the live URL.
See [accounts and channels](ACCOUNTS_AND_CHANNELS.md) for setup and remaining credentials.

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
- Account mode uses verified Supabase email identities or native Google OIDC, plus a
  separate admin approval check. Credentials and user approvals are admin-only.
  Legacy shared-password mode retains salted PBKDF2, throttling and session expiry.
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
- Accounts/channels: `core/accounts.py`, `login_ui.py`, `integrations.py`,
  `settings_ui.py`, `messaging.py`, `messaging_ui.py`, `schedule_ui.py`;
  tests in `test_account_ui.py` and `test_accounts_messaging_schedule.py`.

## Migrations

Local SQLite adds `attempts`, `retry_at` and `retryable` to existing delivery tables
without deleting data. `supabase/schema.sql` and `supabase/002_atomic_workspace.sql`
were applied together in a transaction through the authenticated Supabase CLI.
The live schema check passes. Bootstrap uploaded the campaign and outreach stores;
downloaded copies pass SQLite integrity checks and contain 1,005 qualified domains,
6,510 review records, two campaigns and zero email deliveries. Local sending is
locked after migration. Do not rerun bootstrap: cloud snapshots now exist and must
not be overwritten.

## Test results

- **110 automated tests passed** using isolated databases and mocked message delivery.
  Includes 1,000-target exact stopping/resume, deduplication, extraction and evidence
  rules, source cooldowns, Sheet preservation and verified writes, caps, suppression,
  reply stops, interrupted-send recovery, concurrency, retries, WAL backup restore,
  stale-owner rejection, password login/logout, missing-auth protection and UI pages.
  New coverage checks member approval/revocation, admin-only settings, secret input
  masking, verified Google identities, WhatsApp consent/dedup/caps/uncertain sends,
  timezone/weekday scheduling, DST gaps and configurable sync intervals.
- Python compilation, `pip check` and `git diff --check` passed.
- Live Sheet read-back: 1,005 rows / 1,005 domains / zero missing qualified leads.
- Gmail SMTP authentication and readonly IMAP checks passed locally. **No real
  debugging email or outreach email was sent.** Delivery ledger remains empty.
- No Node frontend, REST server, lint/type-check configuration or build script is
  present. Python tests, compilation and Streamlit AppTest are the applicable checks.
- Live Supabase checks passed for lease acquisition, conflicting-owner rejection,
  renewal, invalid-owner rejection, fenced snapshot writes and backup restore.
  Anonymous table reads expose no rows and anonymous lease RPC calls are denied.
  Hosted SMTP/IMAP and client access still need verification on the deployed host.
- GitHub's Linux CI passed for code release `3c0bf3f`. The real local configuration
  renders login/signup with no exception and hides the workspace before login.
- Production-mode AppTest restored the real cloud backup into temporary databases,
  confirmed 1,005 qualified leads and rendered account login/signup successfully.
  This check ran locally; it does not replace checks on the Streamlit host.

## Security

Private `.env`, credentials, databases, backups, generated deployment secrets and
`.streamlit/secrets.toml` are ignored by Git. A source-file secret-pattern scan found
no private keys or token patterns. Supabase secrets remain server-side; SQL enables
RLS and restricts lease/checkpoint functions to the service role. There is one shared
client workspace, not tenant isolation. Provider messages/credentials are not exposed
in user-facing connection errors.

## Remaining / manual configuration

1. Configure a Discord bot and allowed channels under **Settings > Social sources**
   for direct Discord research. No client-specific communities were supplied.
2. The configured administrator account exists and its login works. Approve intended
   client accounts. Google OAuth, Twilio and Meta credentials remain unconfigured.
3. Verify Sheet access and Gmail authentication from the hosted app before outreach.
   Share the resulting URL only after those checks pass. Real delivery can
   be checked later with explicit authorization; current authorization is auth only.

Exact commands and private-file locations: [deployment guide](DEPLOY_STREAMLIT.md).

## Production readiness

Cloud data is migrated; hosted admin login and Social research rendering are verified.
Actual client account access and hosted message transport remain separate checks.
Community Cloud may sleep and free providers impose limits. A 1,000-lead target is
not a guarantee across every niche/region, nor proof of mailbox deliverability.
Discovery can repeat its last 30-second research batch after abrupt cloud shutdown;
deduplication protects saved results. Cloud sending stops on a lost lease or failed
checkpoint and requires a restart/reconciliation. No paid API is required.
