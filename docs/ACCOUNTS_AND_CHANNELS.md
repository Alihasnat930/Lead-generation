# Accounts, API connections and schedules

The current workspace admin is **alihasnat.dev@gmail.com**. `APP_AUTH_MODE=accounts`
enables the new login/signup page. `APP_ADMIN_EMAILS` is the server-controlled list
of admins; ordinary users cannot promote themselves.

## Sign in and sign up

- Email/password uses Supabase Auth with the publishable/anon key. Create an account,
  verify the email Supabase sends, then sign in. No test account or verification email
  was created during implementation.
- Other verified users may request access. The admin approves or revokes exact email
  addresses under **Settings → User access**. A pending or revoked user cannot see
  leads or use outreach. Approved users have full campaign/outreach/schedule controls;
  only admins can edit provider credentials and user approvals.
- Existing accounts sign in with their Supabase password, not the old shared dashboard
  password. Legacy password mode remains available only when explicitly configured.
- Set the Supabase Auth Site URL and allowed redirect URL to `APP_PUBLIC_URL` in your
  Supabase dashboard. Keep email confirmation enabled. For local development use
  `http://localhost:8501`; update it to the actual HTTPS address when deployed.

## Google sign-in

1. Create an OAuth **Web application** client in Google Auth Platform.
2. Register `http://localhost:8501/oauth2callback` for local development and your
   actual `https://<app-host>/oauth2callback` for deployment.
3. Sign in with email/password as admin, then open **Settings → Google sign-in**.
   Save the client ID, client secret and exact redirect URI. The app generates a
   strong cookie secret and writes the supported Streamlit `[auth.google]` settings.
4. Reload or restart the app if Streamlit has not loaded the new authentication
   configuration. Click **Continue with Google**. Google verifies the email; the same
   admin/approval list still applies.

OAuth state, nonce and identity cookies are handled by Streamlit/Authlib. The app
requires Google's expected issuer and a verified email. No custom callback exchange
or token copied from a URL is accepted. This Google OAuth client is separate from
the Google Sheets service-account key and Supabase's optional Google provider.

See [Streamlit authentication](https://docs.streamlit.io/develop/concepts/connections/authentication)
and [st.login configuration](https://docs.streamlit.io/develop/api-reference/user/st.login).

## Twilio and Meta WhatsApp

Configure either or both under **Settings**:

| Provider | Required fields |
|---|---|
| Twilio WhatsApp | Account SID, auth token, enabled WhatsApp sender, approved Content SID |
| Meta WhatsApp Cloud API | Access token, phone number ID, supported Graph API version, approved template name and language |

Secret inputs remain empty on load; leaving a secret blank preserves it. Values are
kept in server-side settings and the private workspace backup, never activity logs.
**Check authentication** performs a read-only account/phone check; it does not send.

Under **WhatsApp outreach**, record the recipient's opt-in source/date, enter their
international phone number and the approved template's text variables, then queue
the message. It can be sent manually or by the schedule. Scraped phone numbers are
not automatically eligible. Revoking opt-in blocks queued messages.

Identical recipient/template requests are deduplicated within a day. A reservation
is durable before sending. Provider IDs are saved as **accepted**, not delivered.
Timeouts/ambiguous responses are **uncertain** and never automatically retried.
Provider delivery webhooks are not implemented in this release; use Twilio/Meta
logs to investigate delivery or uncertain submissions. Meta supports ordered text
body variables here; media/header/button template components are not implemented.

Messages use the provider's account and billing. Integration code and discovery do
not require a paid model API; Twilio/Meta messaging is not represented as free.
See [Twilio WhatsApp API](https://www.twilio.com/docs/whatsapp/api) and
[Meta Message API](https://developers.facebook.com/documentation/business-messaging/whatsapp/reference/whatsapp-business-phone-number/message-api).

## Schedule page

Choose an IANA timezone, weekdays, sending time, initial/follow-up switches, minimum
score and email limits. WhatsApp has its own opt-in switch and daily limit. Sheet
sync has an independent switch and a 1–60 minute interval. **Pause all automatic
sending** disables both email and WhatsApp, while Sheet sync can continue.

The worker reads these saved settings each tick and records one scheduled cycle per
chosen local day. Repeated ticks/restarts cannot repeat that cycle. Limits include
earlier runs and reset at midnight Asia/Karachi; selecting another schedule timezone
changes when work starts, not the account's allowance boundary. Spring-forward gaps
move to the next valid wall-clock minute. Sleeping hosts or busy workers can delay
the displayed eligible time. The local launcher starts the service; cloud deployments
may also use the existing gated GitHub Actions workflows.

## Current external configuration

GitHub CLI is authenticated as **Alihasnat930**. Supabase's publishable key is mapped
to `SUPABASE_ANON_KEY`, and its live Auth endpoint responds with email confirmation
enabled. The new secret key is also configured through `SUPABASE_SECRET_KEY`; the
project still needs both SQL migrations before cloud storage can start. Google OAuth, Twilio and Meta
credentials have not been supplied, so real provider login/message submission has
not been verified. Add them through the admin Settings forms or private environment.

No email, SMS or WhatsApp message was sent during implementation or testing.
