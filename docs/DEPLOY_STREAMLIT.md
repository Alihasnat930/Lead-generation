# Free client deployment

This is one shared workspace protected by verified accounts and admin approval. Every approved client can run
campaigns, export leads, change settings and control outreach using the configured
sender and CRM. Provider credentials and user approvals are admin-only. It is not a separate database/account for each customer.

## First deployment

1. In your free Supabase project's SQL editor, run `supabase/schema.sql`, then
   `supabase/002_atomic_workspace.sql`. The second migration provides atomic leases
   and rejects stale checkpoint writers. RLS is enabled; no anonymous policies exist.
2. Add `SUPABASE_URL` and `SUPABASE_SERVICE_ROLE_KEY` to your private local `.env`.
   Use the server secret/service-role key, not a publishable/anonymous key.
3. With local sending disabled and no cloud app running, run:

   ```powershell
   .\.venv\Scripts\python.exe scripts\push_cloud_state.py
   .\.venv\Scripts\python.exe scripts\prepare_release.py
   ```

   Bootstrap refuses to overwrite an existing cloud workspace. Keep a backup of
   local `data`. After migration use the cloud workspace for sending; do not run
   independent local and cloud sending against the same sender/Sheet.
4. Sign in at https://share.streamlit.io and deploy repository
   `Alihasnat930/Lead-generation`, branch `main`, main file **`streamlit_app.py`**.
   In Advanced settings choose Python **3.11**, then paste the contents of the private
   `data/deployment/streamlit-secrets.toml` into Secrets. Do not commit that file.
5. Open the resulting URL. With `APP_AUTH_MODE=accounts`, create/verify an account
   for the address in `APP_ADMIN_EMAILS`, then sign in with that Supabase password.
   Set Supabase Auth's Site URL/allowed redirects and `APP_PUBLIC_URL` to the live URL.
   The generated shared password is used only in legacy password mode.
   Use **Check Gmail authentication and Sheet access**; it sends no email. Confirm
   1,005 qualified leads are restored and **Sync qualified leads** verifies the CRM.
   Approve the intended client's verified email under Settings, then share the URL.
6. Automatic email and WhatsApp are initially disabled. Configure the dedicated
   Schedule page when you intend real outreach. Manual send buttons are available
   to approved workspace users. See [accounts and channels](ACCOUNTS_AND_CHANNELS.md).

## Runtime and limits

- One cloud instance owns the workspace lease. Other dashboards/Actions runners
  wait rather than overwrite its history. A stopped owner expires after two minutes.
- SQLite backups include WAL transactions. Discovery checkpoints every 30 seconds;
  an abrupt shutdown can repeat that last bounded research batch. Resume a campaign
  after a restart. Domain/email deduplication prevents repeat qualified results.
- Outreach changes and reservations are saved to Supabase synchronously. The app
  refuses further sends if a checkpoint or lease fails. An interrupted send is
  reconciled against Gmail Sent using its original Message-ID, never blindly retried.
- Community Cloud may sleep; it is not an always-on service guarantee. Optional
  Actions jobs run while the dashboard is inactive. Both use the same saved schedule.
- The deployed host must be able to reach Gmail SMTP 465 and IMAP 993. Local
  authentication passing does not prove connectivity from the hosted environment.
- On a cloud storage/lease error, reboot the Streamlit app to restore current data.
  Secrets live only on the server. Rotate the workspace password by changing its
  hash and rebooting, then distribute the new password privately.

## Optional GitHub Actions

Schedules are gated off by default. Add repository secrets `SUPABASE_URL`,
`SUPABASE_SERVICE_ROLE_KEY`, `GOOGLE_SHEET_ID`, `SERVICE_ACCOUNT_JSON`,
`GMAIL_ADDRESS`, `GMAIL_APP_PASSWORD`. Set `DISCOVERY_JOB_ID` to a saved campaign.
Set repository variable `ENABLE_DISCOVERY_JOBS=true` and/or
`ENABLE_OUTREACH_JOBS=true` only after connection checks pass. Outreach also requires
the app's saved **Enable daily automatic outreach** setting. Scheduling uses
Asia/Karachi, maximum 20 initial + 10 follow-up emails and 30 total per day, counting
other Gmail activity conservatively. GitHub cron execution can be delayed.

First verify using automated fixtures and authentication-only checks. Real delivery
has not been tested and is intentionally outside the debugging authorization.
