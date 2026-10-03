# Prospect Studio

A local lead-research workspace for AI automation and web-development services. Discover businesses, check public contact pages, qualify against observed evidence, and save resumable campaigns to SQLite.

## Start

Double-click **`run_app.bat` in the main project folder**. It works from any current directory, including paths containing spaces. Python **3.11 or newer** must already be installed; the launcher prefers Python 3.11 when creating an environment.

The launcher creates `.venv` when needed, installs the requirements, checks the application and database, starts the local dashboard, and opens your browser. Later launches reuse the environment. A second launch opens the existing dashboard instead of starting another server. First setup needs internet to download missing Python packages; no API keys or paid services are required.

From PowerShell:

```powershell
.\run_app.bat
```

Open <http://localhost:8501>. Keep the launcher window open while using the dashboard. Campaigns start their own background worker when you click **Start campaign** or **Resume**; no separate worker terminal is required. Closing the browser does not stop a campaign. Pause campaigns in the dashboard when you want scraping to stop. `Ctrl+C` in the launcher stops the dashboard; already-running campaign workers continue independently.

Useful launcher options:

```powershell
.\run_app.bat --check        # Install/check packages and database, then exit
.\run_app.bat --repair       # Reinstall requirements, then start
.\run_app.bat --port 8502    # Use a different local port
.\run_app.bat --no-browser   # Start without opening a browser tab
```

Stop the existing launcher before using `--check`, `--repair`, or changing ports. Startup errors stay visible in the console; dashboard logs are in `data/logs/server.log` and worker logs are in `data/logs/worker.log`. For unattended scripts, set `PROSPECT_NO_PAUSE=1` to disable the pause on an error. The launcher binds to `127.0.0.1` so the dashboard is local to this computer.

1. No API key or subscription is needed. Choose **Free discovery** as your search source.
2. In **Campaigns**, choose niches and markets, review the limits, and create a campaign.
3. Click **Start campaign**. A background worker continues when the browser closes. Keep the computer awake and connected.
4. Pause or resume as needed. Export from the campaign or **Lead database**.

Discovery runs entirely through free public sources and local Python qualification. There are no paid search or model calls in free mode. Failed requests count toward the request allowance; successful cached searches do not. Internet bandwidth, time and computer resources are still required. Automated tests use isolated fixtures; separately identified live checks use public sources.

## Coverage and volume

- 10 presets: clinics, legal, real estate, accounting, e-commerce, HVAC/home services, dental, recruitment, marketing, and restaurants/salons. Custom niches and search terms are supported.
- **150 cities** across the US, UK and all **27 EU countries**. Choose subsets or add cities as `City | country code`, such as `Bath | gb`.
- English query variations plus local terms for German, French, Spanish, Italian, Dutch, Portuguese and Polish. Other markets use English niche queries with their search locale; multilingual extraction is not comprehensive.
- Queries rotate across countries, cities and niches before deeper search pages. The target is campaign-wide, not a quota per niche or country.
- Defaults: **1,000 qualified leads**, 3,000 discovery attempts, 15,000 candidate reviews, 8 concurrent websites, 3 web-search pages per query, 4 website pages per candidate and a 12-hour session. All are adjustable.

**1,000 is a target, not a guaranteed yield.** Business availability, public contacts, strict qualification, duplicates, blocked sites and provider limits affect results. Only a reached target is marked `complete`. Every shortfall has a reason. Broaden the audience or increase a limit where appropriate; no results are invented to fill the export.

### Sources

- **OpenStreetMap / Overpass:** finds mapped businesses with websites around bundled city centres (12 km radius). Queries group compatible niche tags and return individual business source URLs. Physical stores are e-commerce candidates only; their websites must still show e-commerce evidence. Custom niches without map tags use web search. Map coordinates identify a discovery area; they do not prove a business's address.
- **Bing:** our Python parser reads paginated public results. No key is needed. CAPTCHA or page changes can temporarily stop this source.
- **DuckDuckGo / DDGS:** the [MIT-licensed DDGS project](https://github.com/deedy5/ddgs) supplies another free public search source, with explicit country/language and pagination settings. We select DuckDuckGo explicitly rather than automatically querying all engines.
- Website enrichment follows homepage and contact/about links with bounded downloads, timeouts, robots checks and a per-site delay. Contacts come from visible text, `mailto:`/`tel:` links and structured metadata. Domain inboxes are preferred; published addresses on known public email services are also accepted. Unrelated third-party inboxes are excluded.

CAPTCHA, robots restrictions and inaccessible/JavaScript-only pages are recorded. The crawler does not solve challenges or log in. Free mode gives an unavailable source a 5–60 minute cooldown while other independent sources continue. If all sources are cooling down, the worker waits automatically; pause remains available. A Bing-only campaign pauses on a source failure.

Source requests are sequential, at least 8 seconds apart for each web engine and 15 seconds apart for Overpass. Successful responses are cached for seven days across campaigns. Overpass downloads are bounded to 8 MB per request and at most 100 requests per UTC day, below its broad public-usage guidelines. City coordinates come from a bundled **GeoNames CC BY 4.0** subset, so there is no geocoding API dependency. See [third-party notices](THIRD_PARTY_NOTICES.md) for repository links and attribution. OSM-derived exports include attribution, license and original object URL; preserve these when sharing.

## Qualification

Every qualified lead must pass all gates:

1. A readable business website page was fetched.
2. Website text supports the selected niche.
3. Website evidence supports the target city **and** country, without a conflicting structured country. Country evidence can be an address, country name, country suffix or US state/ZIP format. A search query is never accepted as evidence.
4. A published email is present by default. You can instead allow a published phone or enquiry form.
5. The score reaches the threshold.

Score: readable website **15**, niche **30**, location **20**, email **25** (or phone **15** / form **10**), multiple readable pages **10**. These are research heuristics, not proof of intent, budget, authority or demand.

**Qualification is local and costs no API credits.** The rules evaluate fetched evidence and produce an explained score. Free campaigns reject the paid-model-review option even if an old configuration enables it. Outreach also uses local templates; OpenRouter is optional only for legacy integrations. No language model is needed to scrape, score, draft or export leads.

Emails are labelled `published_unverified`: a public source URL exists, but mailbox deliverability has not been checked. Clearly unsuitable departmental inboxes (press, privacy, careers, billing, etc.) are filtered out. Phone numbers and forms are similarly sourced, not ownership-verified. Contacts are never generated. Service recommendations are hypotheses, not confirmed business problems.

## Persistence and recovery

- Database: `data/prospect_studio.db` (SQLite WAL).
- Domains are unique across campaigns. Subdomains collapse to their registered domain; individual Shopify storefronts remain separate. Multiple branches on one domain count as one business.
- Previously researched domains, including review records, are skipped. Repeating an audience is not a re-enrichment job; broaden it to find new businesses.
- A repeated email cannot count as another qualified lead. CSV includes sources, evidence, reasons, crawl status and timestamps. Formula-like cells are escaped.
- Search cursors and new candidates are checkpointed together. Each reviewed lead is saved immediately.
- Pause finishes the current bounded website batch. Only one worker can run against the database at a time.
- A crashed worker's lease expires after 90 seconds; Resume recovers its queue. An expired worker cannot save results after a replacement claims the campaign.
- `search_limit`, `candidate_limit` and `time_limit` are resumable after raising the limit or starting another session. Free-source cooldowns retry automatically. `exhausted` means the search plan finished; create a broader campaign.
- An in-flight source request may be repeated after a crash because the remote request and local checkpoint are not one transaction. The original attempt still counts toward the local allowance.
- Keep the complete `data` folder for backups; SQLite may have active WAL/SHM files. Stop workers before moving the database.

Credential-bearing URLs, non-HTTP schemes, non-public resolved addresses and cross-domain website redirects are rejected. The local launcher binds to localhost. The hosted entrypoint requires a password and durable cloud storage. Hosted access is one shared workspace, without separate client tenants.

## Google Sheets and outreach

Discovery works independently of Sheets. **Outreach & CRM → Sync qualified leads** appends in batches, skips existing domains/emails, uses the sheet's actual header order, and preserves existing CRM statuses. Share the configured sheet with the service account in `credentials/service_account.json` to enable syncing.

Outreach saves a delivery reservation before SMTP submission, then marks it sent only after Gmail accepts it. Sheet writes are verified and use actual header names. Gmail Sent history, replies, opt-outs, bounces, suppression and daily caps are checked before sending. Interrupted sends block retries until reconciled with Gmail. Definite transient failures allow at most three attempts with exponential backoff; permanent rejections do not retry. Follow-ups wait at least 4 days, then 10 days from the initial email and 6 days after the first follow-up. All emails include LinkedIn, GitHub and Upwork links. Published email addresses do not prove mailbox deliverability or buying intent.

`run_app.bat` starts the dashboard and Sheet sync service. Automatic emails require the separate saved **Enable daily automatic outreach** setting; manual sends do not enable it. The authentication check never sends email. Discovery does not send messages.

Settings are documented in `.env.example`. Free discovery works without an `.env` file; copy the example to `.env` only when configuring optional integrations. Never publish `.env`, service-account keys, backups or your lead database.

## Folder layout

The duplicate nested project folder has been removed. All commands run from this single root:

```text
lead_gen_app/
|-- run_app.bat              # One-command Windows setup and launch
|-- app.py                   # Streamlit dashboard
|-- worker.py                # Background campaign entry point
|-- requirements.txt
|-- README.md
|-- THIRD_PARTY_NOTICES.md
|-- core/                    # Discovery, scoring, storage and integrations
|-- scripts/                 # Launcher and city-data maintenance
|-- tests/                   # Isolated automated checks
|-- assets/                  # Bundled city coordinates
|-- .streamlit/              # Dashboard theme and settings
|-- credentials/             # Optional private service-account key
|-- data/
|   |-- prospect_studio.db   # Saved campaigns and leads
|   |-- logs/               # Dashboard and worker logs
|   |-- runtime/            # Launcher lock and process state
|   |-- samples/            # Previous live-discovery sample
|   `-- cache/              # Generated Python cache files
|-- .venv/                   # Local Python environment (created automatically)
|-- .env                     # Optional private integration settings
|-- .env.example             # Safe settings template
`-- .backups/                # Original code and pre-cleanup data backup
```

The old placeholder file and duplicate dotenv file are archived in `.backups/before-folder-cleanup/`. The active `.env`, service-account key, lead database and campaign checkpoints are preserved. Paths are resolved from the project root, not the terminal's current directory. If moving this project to another computer, keep `data`, `.env` and `credentials` private, install Python there, and let the launcher create a fresh `.venv`.

## Development

## Cloud deployment status

The app includes **login/signup**, admin-approved Google/email access, **Twilio and
Meta WhatsApp settings**, an opted-in template queue, and a dedicated **Schedule**
page. See [accounts and channels](docs/ACCOUNTS_AND_CHANNELS.md) for setup, controls,
provider limits and the distinction between message acceptance and delivery.

Deploy **`streamlit_app.py`**, Python **3.11**, on Streamlit Community Cloud. Follow
[`docs/DEPLOY_STREAMLIT.md`](docs/DEPLOY_STREAMLIT.md) for the two Supabase migrations,
private secrets, database bootstrap and client login. `scripts/prepare_release.py`
generates a password and secrets template under ignored `data/deployment/`.

The hosted app and optional Actions jobs share an exclusive database-enforced lease.
Compressed SQLite backups include WAL transactions. Discovery checkpoints every
30 seconds when changed; outreach changes checkpoint synchronously and fail closed
if cloud storage is unavailable. Schedules are disabled by default in GitHub Actions.
Streamlit sleep and external service limits mean this free deployment is not an
always-on availability guarantee. See the release report for verified versus pending
checks; source code being ready does not mean an app has been published.

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m compileall -q app.py worker.py core scripts
```

Tests use temporary databases and deterministic fixtures. They cover a 1,000-qualified-lead run, exact stopping, resume, deduplication, source failures, cooldowns, cache reuse, daily free-source limits, worker leases, evidence gates, extraction, export and CRM preservation. They prove pipeline behavior, not real-world yield or deliverability.

Key modules: `markets.py` (targeting), `providers.py` (search), `websites.py` (enrichment), `qualification.py` (gates), `store.py` (checkpoints), `campaigns.py` / `worker.py` (runner), `exports.py` (CSV), and `app.py` (dashboard).

Original code, including the unfinished scraper, is preserved in `.backups/before-premium-upgrade/`. Synchronous `pipeline.run_discovery` is retired. Use `create_campaign` and `run_job` from `core.campaigns`; `core.scalable_scraper` re-exports the new entry points.
