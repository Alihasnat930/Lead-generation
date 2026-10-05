# Social research for academic support

The **Social research** page is separate from business discovery, Google Sheets
and outbound messaging. It finds source posts for human review; it does not claim
to identify verified students, their age, location, budget or purchase intent.

## Available collection paths

| Platform | Implemented path | What is needed |
|---|---|---|
| Reddit | Public search-index excerpts and post links; CSV/JSON imports | No key for indexed discovery |
| Facebook | Public search-index excerpts and post links; CSV/JSON imports | No key for indexed discovery; closed groups are not collected |
| Discord | Bot-based history reads in configured server channels; DiscordChatExporter JSON imports | Bot token, allowed channel IDs and server/channel permissions |

The existing MIT-licensed [DDGS adapter](https://github.com/deedy5/ddgs) and our
bounded Bing parser provide indexed discovery. Neither downloads a Reddit or
Facebook discussion thread. Snippets may be incomplete, stale or unavailable.
Source blocks produce a visible cooldown and retain the query for resume.

For direct Reddit collection, [PRAW](https://github.com/praw-dev/praw) is a maintained
BSD-2-Clause Python wrapper. It was evaluated but is **not installed or integrated**:
[Reddit's API terms](https://redditinc.com/policies/data-api-terms) require a separate
agreement for commercial use. Open-source software does not grant platform access.

The MIT-licensed [facebook-scraper](https://github.com/kevinzg/facebook-scraper)
was evaluated; GitHub reported its last push as 22 June 2024 when checked on
6 October 2026. It is not bundled or relied on for client delivery.

## Discord setup

1. Create a bot in the [Discord Developer Portal](https://discord.com/developers/applications).
2. Add it to a server you manage or have permission to monitor. Enable **Message
   Content** intent and grant **View Channel** and **Read Message History** for the
   selected channels. See [Discord's message documentation](https://docs.discord.com/developers/resources/message#get-channel-messages).
3. As workspace admin, open **Settings → Social sources**. Save the bot token and
   comma-separated channel IDs. The authentication check sends no message.
4. Create a new research run with Discord selected. Allowed channels are captured
   in its query plan and checked against current admin settings on every batch.
   Removing a channel from Settings stops further reads of it.

The adapter uses Discord's HTTP API directly with a bot authorization header.
[discord.py](https://github.com/Rapptz/discord.py) was evaluated as an MIT-licensed
SDK option; it is not bundled. No user-account token, DM collection, server joining,
member export or automatic messaging is implemented.

## Research workflow

Choose platforms, UK/US search markets, topics and up to eight extra phrases.
Presets cover proofreading, thesis editing, writing tutoring, research guidance
and referencing. Search geography is never treated as a person's location.
UK curriculum, Harvard, APA and other textual mentions are shown as evidence,
not as proof of a particular curriculum or institution.

The default **1,000-record target** is a cap on relevant research posts. It is not
a promise of 1,000 qualified customers. A run also has a request allowance and a
page limit. Request batches include a web-search page or up to 100 Discord
messages (which may require multiple HTTP requests). A session pauses after an
hour. Pause/resume keeps query pages and Discord cursors; raising limits resumes
remaining work. If all sources are cooling down, retry later. Exhausted searches
require a broader new run or an import. Old records can appear in multiple runs
but the same source URL cannot appear twice within a run.

Posts receive a transparent heuristic score: academic context 25, matching topic
or phrase 25, possible request wording 30, and explicit selected-market mention 20.
Advertisements are classified separately. Every post initially needs human review.
Mark it **Relevant** or **Dismissed**; review status is shared across runs for the
same source URL. Emails and phone-like numbers in excerpts are redacted, and author
profiles are not stored. No social record enters an outreach or CRM queue.

## Imports and exports

Upload UTF-8 CSV or JSON up to 5 MB / 10,000 rows. Standard fields are `url`, `title`,
`text`, `posted_at` (ISO timestamp with timezone). JSON accepts an array of objects
or `{ "posts": [...] }`. Imports must contain supported post links and match the
run's chosen platforms/topics. Other rows are counted as skipped.

Server-channel JSON exports from [DiscordChatExporter](https://github.com/Tyrrrz/DiscordChatExporter)
are also accepted. Its exporter is not bundled; use bot-based exports from channels
you are allowed to collect. The importer ignores authors, member lists and attachments.

CSV exports include links, snippets, provenance, timestamps, scores, evidence and
review status. Formula-like cell values are escaped. Social tables live in the
existing campaign SQLite file and are included in its Supabase backups. Each saved
batch checkpoints through the existing cloud lease; no Supabase migration is needed.

## Verification and external requirements

Automated fixtures cover source URL validation, contact redaction, evidence scoring,
advertisement handling, import bounds, deduplication, a 1,000-record cap, partial-page
resume, stale-writer rejection, cloud checkpoint hooks, source failures, Discord
permission checks and dashboard rendering. Fixtures are not real student leads.

On 6 October 2026, one live DDGS query returned eight matching Reddit post links;
Bing parsed successfully but returned no matching post links for the same query.
This was a bounded adapter check, not a qualified-lead or volume validation.
The Facebook DDGS probe reported source unavailability, so live Facebook discovery
has not been verified. No Discord bot credentials were available for a live test.
The complete Python suite passed **110 tests**, including 16 new social checks.

No client-selected communities or Discord bot credentials were available during
implementation. Live platform access and usable volume depend on those sources;
no unattended social research campaign or message delivery is enabled by this release.
