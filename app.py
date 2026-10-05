"""Prospect Studio: local, persistent business lead research."""
import time
from pathlib import Path
import pandas as pd
import streamlit as st
from core import campaigns, sheets, pipeline, email_sender
from core.config import config, verify_app_password
from core.exports import csv_bytes
from core.markets import NICHES, COUNTRIES, locations_for, plan_queries
from core.mailbox import Mailbox
from core.outreach_store import OutreachStore
from core.supabase_client import check_schema as check_supabase_schema
from core.store import Store

st.set_page_config(page_title="Prospect Studio", page_icon="\u25ce", layout="wide", initial_sidebar_state="expanded")
identity=None
workspace_role='member'
if config.APP_AUTH_MODE=='accounts':
    from core.login_ui import require_identity, require_access
    from core.integrations import sync_google_secrets
    if config.CLOUD_MODE:
        from core.cloud_runtime import start_cloud_runtime
        try:
            start_cloud_runtime()
        except Exception as exc:
            st.error(str(exc) if isinstance(exc,ValueError) else 'Configure Supabase server storage and apply the cloud migrations before opening this workspace.')
            st.stop()
    account_store=OutreachStore()
    try:
        sync_google_secrets(account_store)
    except Exception:
        st.warning('Google sign-in configuration needs attention. Email sign-in remains available.')
    identity=require_identity()
    workspace_role=require_access(identity,account_store)
    st.session_state['workspace_identity']=identity
elif config.CLOUD_MODE and not config.APP_LOGIN_PASSWORD_HASH:
    st.error('Deployment setup required: configure APP_LOGIN_PASSWORD_HASH in Streamlit Secrets before opening this workspace.')
    st.stop()
if config.APP_AUTH_MODE!='accounts' and st.session_state.get('authenticated') and time.time()-st.session_state.get('authenticated_at',0)>8*3600:
    st.session_state['authenticated']=False
if config.APP_AUTH_MODE!='accounts' and config.APP_LOGIN_PASSWORD_HASH and not st.session_state.get("authenticated"):
    st.title("Prospect Studio")
    st.subheader("Private dashboard")
    password = st.text_input("Password", type="password", key='login_password')
    if st.button("Sign in", type="primary"):
        from core.auth import authenticate
        accepted,message=authenticate(password)
        if accepted:
            st.session_state["authenticated"] = True
            st.session_state['authenticated_at']=time.time()
            del st.session_state['login_password']
            st.rerun()
        st.error(message)
    st.stop()
if config.CLOUD_MODE:
    from core.cloud_runtime import start_cloud_runtime
    try:
        start_cloud_runtime()
    except Exception as exc:
        st.error(str(exc) if isinstance(exc,ValueError) else 'Cloud storage setup is incomplete or unavailable. Check Supabase secrets and apply both SQL migrations.')
        st.stop()
st.markdown("""<style>
@import url('https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700&family=Manrope:wght@500;600;700;800&display=swap');
html, body, [class*="css"], .stApp {font-family: 'DM Sans', sans-serif;}
h1,h2,h3 {font-family: 'Manrope', sans-serif !important; letter-spacing:-.035em;}
.stMainBlockContainer {padding-top:4rem; max-width:1480px;}
[data-testid="stSidebar"] {background:#122F35;}
[data-testid="stSidebar"] * {color:#E6F1EE;}
[data-testid="stSidebar"] [data-testid="stCaptionContainer"] * {color:#9DB8B6;}
[data-testid="stSidebar"] hr {border-color:#2C484D;}
[data-testid="stMetric"] {background:#fff;border:1px solid #E3E9ED;border-radius:14px;padding:18px 20px;}
[data-testid="stMetricLabel"] {color:#657B83; font-size:12px;}
[data-testid="stMetricValue"] {font-family:'Manrope',sans-serif;font-weight:800;color:#183C40;}
[data-testid="stVerticalBlockBorderWrapper"]>div {border-radius:14px;}
.stButton>button {border-radius:9px; font-weight:600;}
.eyebrow {font-size:11px;letter-spacing:2px;color:#668A84;font-weight:700;margin-bottom:8px;}
.brand {font-family:'Manrope',sans-serif;font-size:24px;font-weight:800;letter-spacing:-1px;margin:4px 0;}
.brand span {color:#89DEBD !important;}
.hero {background:linear-gradient(115deg,#173F42,#215F59);border-radius:18px;padding:30px 34px;margin:8px 0 24px;color:#EDF9F5;}
.hero h2 {font-size:31px;margin:0 0 8px;color:#fff;}
.hero p {max-width:740px;color:#C1DBD5;font-size:15px;margin:0;line-height:1.65;}
.hero .eyebrow {color:#A6DDCB;}
.pill {display:inline-block;background:#EFF7F3;color:#227059;padding:5px 11px;border-radius:20px;font-size:12px;font-weight:600;margin-right:6px;}
.step {color:#79938F;font-size:11px;letter-spacing:1.5px;font-weight:700;}
.small-note {color:#72848D;font-size:13px;}
</style>""", unsafe_allow_html=True)

store = Store()
outreach_store = OutreachStore()
if identity is None:
    from core.accounts import admins
    owner=next(iter(sorted(admins())),config.GMAIL_ADDRESS)
    identity={'email':owner,'subject':'legacy-local-owner','provider':'legacy','verified':True}
if config.CLOUD_MODE:
    @st.cache_resource
    def start_hosted_services():
        from core.outreach_scheduler import launch
        launch()
        return True
    start_hosted_services()
with st.sidebar:
    st.markdown('<div class="brand"><span>&#9678;</span> Prospect Studio</div>', unsafe_allow_html=True)
    st.caption("BUSINESS DISCOVERY WORKSPACE")
    st.divider()
    page = st.radio("Workspace", ["Campaigns", "Lead database", "Social research", "Outreach & CRM", "WhatsApp outreach", "Schedule", "Settings"], label_visibility="collapsed")
    st.divider()
    st.markdown("**Your markets**")
    st.caption("United States \u00b7 United Kingdom \u00b7 European Union")
    st.markdown("**Your workspace**")
    st.caption(("Cloud backups" if config.CLOUD_MODE else "Local storage")+" \u00b7 Persistent jobs \u00b7 Source evidence")
    if config.APP_AUTH_MODE=='accounts':
        st.caption(identity['email']+' · '+workspace_role)
    if (config.APP_LOGIN_PASSWORD_HASH or config.APP_AUTH_MODE=='accounts') and st.button('Sign out'):
        if config.APP_AUTH_MODE=='accounts':
            from core.login_ui import logout
            logout()
        st.session_state.clear()
        st.rerun()
    st.divider()
    st.caption("Free discovery: OpenStreetMap + web search")
    st.caption("Qualification: local scoring, no paid model")


def safe_error(exc):
    if isinstance(exc, (ValueError, PermissionError)):
        st.error(str(exc))
    else:
        st.error(f"This action failed ({type(exc).__name__}). Check your connection and settings; saved leads remain available.")


def show_metrics(stats):
    a, b, c, d = st.columns(4)
    a.metric("Qualified leads", f'{stats["qualified"]:,}')
    b.metric("Candidates reviewed", f'{stats["processed"]:,}')
    c.metric("Needs review", f'{stats["review"]:,}')
    d.metric("Qualification rate", f'{stats["qualified"] / stats["processed"]:.0%}' if stats["processed"] else "-")


def display_leads(leads, prefix):
    if not leads:
        st.info("No matching leads yet. Results appear here as websites are checked.")
        return
    rows = [{key: lead.get(key, "") for key in ("company_name", "industry", "country", "email", "phone", "lead_score", "qualification_status", "website")} for lead in leads]
    st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True,
                 column_config={"website": st.column_config.LinkColumn("Website"),
                                "lead_score": st.column_config.ProgressColumn("Fit score", min_value=0, max_value=100),
                                "qualification_status": "Qualification", "company_name": "Business"})
    st.download_button("Download these leads \u00b7 CSV", csv_bytes(leads), f"prospects-{prefix}.csv", "text/csv", key=f"export-{prefix}")
    with st.expander("Inspect qualification evidence"):
        options = {lead["lead_id"]: lead for lead in leads}
        selected = st.selectbox("Business", list(options), format_func=lambda key: options[key]["company_name"], key=f"inspect-{prefix}")
        lead = options[selected]
        st.write(" \u00b7 ".join(lead.get("qualification_reasons", [])))
        st.caption(f'Location: {lead.get("location_status", "")} | Email: {lead.get("email_status", "")} | AI: {lead.get("ai_status", "")}')
        if lead.get("evidence"):
            st.dataframe(pd.DataFrame(lead["evidence"]), width="stretch", hide_index=True,
                         column_config={"url": st.column_config.LinkColumn("Evidence source")})
        st.write("Suggested service: " + lead.get("recommended_service", ""))
        st.caption("Service suggestions describe potential fit. They do not establish buying intent or a confirmed business problem.")
        if lead.get("ai_reason"):
            st.write(lead["ai_reason"])
        if lead.get("crawl_errors"):
            st.caption("Website checks: " + ", ".join(lead["crawl_errors"]))


def campaign_builder():
    st.markdown('<span class="step">01 / AUDIENCE</span>', unsafe_allow_html=True)
    left, right = st.columns([1.25, 1])
    with left:
        name = st.text_input("Campaign name", value="US \u00b7 UK \u00b7 EU growth campaign")
        niches = st.multiselect("Business niches", list(NICHES), default=list(NICHES))
        custom_niche = st.text_input("Additional niche (optional)", placeholder="e.g. veterinary practices")
        keywords = st.text_input("Additional search and qualification terms", placeholder="Comma-separated, relevant to the selected niches")
    with right:
        regions = st.multiselect("Markets", ["US", "UK", "EU"], default=["US", "UK", "EU"])
        country_options = [code for code, values in COUNTRIES.items() if values[1] in regions]
        countries = st.multiselect("Limit to countries (empty = all selected markets)", country_options,
                                   format_func=lambda code: COUNTRIES[code][0])
        available = locations_for(regions, countries)
        labels = {f'{loc["city"]}, {loc["country"]}': loc for loc in available}
        city_labels = st.multiselect("Limit to cities (empty = full city pack)", list(labels))
        locations = [labels[label] for label in city_labels] if city_labels else available
        custom_cities = st.text_area("Extra cities \u00b7 one per line: City | country code", height=80,
                                    placeholder="San Jose | us\nBath | gb\nDortmund | de")
        invalid_cities = []
        for line in custom_cities.splitlines():
            if not line.strip():
                continue
            parts = [part.strip() for part in line.split("|")]
            if len(parts) != 2 or parts[1].lower() not in country_options or not parts[0]:
                invalid_cities.append(line)
                continue
            city, code = parts[0], parts[1].lower()
            country, region, language, _ = COUNTRIES[code]
            locations.append(dict(city=city, country=country, country_code=code, region=region, language=language))
        locations = list({(loc["city"].lower(), loc["country_code"]): loc for loc in locations}.values())
    st.divider()
    st.markdown('<span class="step">02 / QUALITY & VOLUME</span>', unsafe_allow_html=True)
    a, b, c = st.columns(3)
    with a:
        target = st.number_input("Qualified lead target", min_value=1, max_value=100000, value=1000, step=100)
        min_score = st.slider("Minimum fit score", 0, 100, 75)
    with b:
        provider = st.selectbox("Search source", ["free", "bing"], format_func=lambda value: {"free": "Free discovery · OSM + Bing + DuckDuckGo", "bing": "Bing only · public search"}[value])
        require_email = st.checkbox("Require a published business email", value=True)
    with c:
        use_ai = False
        st.write("Local evidence scoring")
        st.caption("No model subscription or API key. Every lead must pass the niche, location and contact checks.")
    with st.expander("Run limits and performance", expanded=False):
        a, b, c = st.columns(3)
        search_limit = a.number_input("Maximum discovery requests", 1, 100000, 3000 if provider == "free" else 300,
                                      key=f"search-budget-{provider}", help="Free source requests, including retries. Cached searches do not use this allowance.")
        candidate_limit = b.number_input("Maximum candidates to review", 1, 200000, 15000, step=1000)
        ai_limit = 0
        runtime_hours = c.number_input("Maximum hours per session", 1, 72, 12)
        workers = a.slider("Concurrent websites", 1, 16, 8)
        max_pages = b.slider("Search pages per query", 1, 10, 3)
        website_pages = c.slider("Pages checked per website", 1, 6, 4)
    target_service = st.text_input("Services to match", value="AI automation, chatbots, RAG, n8n/Python workflows and web development")
    settings = dict(name=name.strip(), niches=niches + ([custom_niche.strip()] if custom_niche.strip() else []),
                    locations=locations, keywords=[v.strip() for v in keywords.split(",") if v.strip()],
                    target=int(target), min_score=int(min_score), provider=provider, require_email=require_email,
                    use_ai=use_ai, max_search_calls=int(search_limit), max_candidates=int(candidate_limit),
                    max_ai_calls=int(ai_limit), workers=workers, max_pages=max_pages, website_pages=website_pages,
                    target_service=target_service, max_runtime_hours=int(runtime_hours))
    planned = len(plan_queries(settings)) if settings["niches"] and locations else 0
    st.markdown(f'<span class="pill">{len(settings["niches"])} niches</span><span class="pill">{len(locations)} cities</span><span class="pill">{planned:,} search combinations</span>', unsafe_allow_html=True)
    st.caption(f"Up to {min(search_limit, planned * max_pages):,} initial search requests, with retries sharing the same request cap. Target volume depends on available businesses, public contacts and provider coverage.")
    if provider == "bing":
        st.info("Bing-only runs pause if public search blocks requests. Free discovery can continue with independent sources.")
    else:
        st.info("No API keys or paid services needed. Results are cached; busy sources cool down automatically while other sources continue.")
        st.caption("OpenStreetMap covers mapped businesses with websites. Custom niches and additional cities also use web search.")
    if invalid_cities:
        st.error("Use a valid city and country code from your selected markets: " + "; ".join(invalid_cities))
    if st.button("Create campaign", type="primary", disabled=bool(invalid_cities), width="stretch"):
        try:
            job_id = campaigns.create_campaign(settings, store)
            st.session_state["selected_job"] = job_id
            st.session_state["created_campaign"] = True
            st.rerun()
        except Exception as exc:
            safe_error(exc)


@st.fragment(run_every=5)
def campaign_progress(job_id):
    job, stats = store.job(job_id), store.stats(job_id)
    settings = job["settings"]
    running = job["status"] in ("running", "starting") and job["heartbeat"] > time.time() - 90
    st.subheader(job["name"])
    st.caption(f'{job["status"].replace("_", " ").title()} \u00b7 {settings["provider"]} \u00b7 created {job["created_at"][:16]} UTC')
    show_metrics(stats)
    st.progress(min(stats["qualified"] / settings["target"], 1), text=f'{stats["qualified"]:,} of {settings["target"]:,} qualified leads')
    a, b, c = st.columns(3)
    a.caption(f'Search requests: {job["search_calls"]:,} / {settings["max_search_calls"]:,}')
    b.caption("Local qualification · no paid AI calls")
    c.caption(f'Duplicates skipped: {job["duplicates"]:,} \u00b7 queued candidates: {stats["pending"]:,}')
    if job["reason"]:
        (st.success if job["status"] == "complete" else st.info)(job["reason"])
    if job["status"] in ("running", "starting") and not running:
        st.warning("The worker is no longer responding. Resume to recover its saved queue.")
    left, right = st.columns([1, 3])
    if running:
        if left.button("Pause campaign", key=f"pause-{job_id}", disabled=bool(job["stop_requested"])):
            store.request_pause(job_id)
            st.rerun(scope="fragment")
        if job["stop_requested"]:
            right.caption("Pausing after the current website batch finishes.")
        else:
            right.caption("Working in the background. You can navigate away or close this browser; keep the computer awake.")
    elif job["status"] not in ("complete", "exhausted"):
        if left.button("Start campaign" if job["status"] == "ready" else "Resume campaign", type="primary", key=f"start-{job_id}"):
            try:
                campaigns.launch(job_id, store)
                st.rerun(scope="fragment")
            except Exception as exc:
                safe_error(exc)
    with st.expander("Campaign settings and limits"):
        st.write("Niches: " + ", ".join(settings["niches"]))
        st.caption(f'{len(settings["locations"])} cities \u00b7 {settings["workers"]} concurrent websites \u00b7 {settings["website_pages"]} pages per website')
        st.caption("Score: 15 accessible website + 30 niche evidence + 20 city/country evidence + 25 email (or 15 phone / 10 form) + 10 for multiple pages. Every qualification gate must also pass.")
        if not running and job["status"] != "complete":
            with st.form(f"limits-{job_id}"):
                source = st.selectbox("Search provider", ["free", "bing"], index=0 if settings["provider"] == "free" else 1)
                budget = st.number_input("Total search request allowance", 1, 100000, settings["max_search_calls"])
                candidates = st.number_input("Total candidate review allowance", 1, 200000, settings["max_candidates"])
                ai_budget = 0
                if st.form_submit_button("Save limits"):
                    try:
                        store.revise_limits(job_id, budget, candidates, source, ai_budget)
                        st.rerun(scope="fragment")
                    except Exception as exc:
                        safe_error(exc)
    with st.expander("Activity log", expanded=running):
        events = store.events(job_id)
        if events:
            st.dataframe(pd.DataFrame(events), hide_index=True, width="stretch")
    health = store.source_health(job_id)
    if health:
        with st.expander("Free source status", expanded=True):
            st.dataframe(pd.DataFrame([{"Source": h["source"], "Retry in (minutes)": round(max(0, h["blocked_until"] - time.time()) / 60, 1), "Reason": h["reason"]} for h in health]), hide_index=True, width="stretch")
    st.caption("Map data: © OpenStreetMap contributors (ODbL). City coordinates: GeoNames (CC BY 4.0).")
    qualified = store.leads(job_id, "QUALIFIED")
    if qualified:
        st.download_button("Download qualified leads \u00b7 CSV", csv_bytes(qualified), f"qualified-{job_id}.csv", "text/csv", key=f"quick-{job_id}")
        st.dataframe(pd.DataFrame([{k: lead.get(k, "") for k in ("company_name", "country", "email", "lead_score")} for lead in qualified[:25]]), hide_index=True, width="stretch")
    elif stats["processed"]:
        st.caption("Candidates are saved in the Lead database, including evidence and reasons for review.")


if page == "Campaigns":
    st.markdown('<div class="eyebrow">DISCOVER / QUALIFY / GROW</div>', unsafe_allow_html=True)
    st.title("Your next clients, in focus.")
    st.markdown('<div class="hero"><div class="eyebrow">BUILT FOR YOUR NEXT 1,000</div><h2>Find businesses that fit your work.</h2><p>Research across the US, UK and EU. Turn public business websites into a focused pipeline, with contact sources and a clear reason behind every qualified lead.</p></div>', unsafe_allow_html=True)
    jobs = store.jobs()
    with st.expander("+ Build a new campaign", expanded=not jobs):
        campaign_builder()
    if st.session_state.pop("created_campaign", False):
        st.success("Campaign saved. Review it below and start when ready.")
    if jobs:
        job_map = {job["id"]: job for job in jobs}
        if st.session_state.get("selected_job") not in job_map:
            st.session_state["selected_job"] = next((job["id"] for job in jobs if job["status"] in ("running", "starting")), jobs[0]["id"])
        job_id = st.selectbox("Campaign", list(job_map), key="selected_job", format_func=lambda key: f'{job_map[key]["name"]} \u00b7 {key[:6]}')
        campaign_progress(job_id)
    else:
        a, b, c = st.columns(3)
        with a:
            st.markdown("**01  Define your audience**")
            st.caption("Choose niche, country and city coverage. Add custom niches whenever needed.")
        with b:
            st.markdown("**02  Research with evidence**")
            st.caption("Check business websites and contact pages. Deduplicate across every campaign.")
        with c:
            st.markdown("**03  Build your pipeline**")
            st.caption("Export qualified leads with their sources, or sync new records to your CRM.")

elif page == "Lead database":
    st.markdown('<div class="eyebrow">YOUR RESEARCH, SAVED</div>', unsafe_allow_html=True)
    st.title("Lead database")
    show_metrics(store.stats())
    st.write("")
    jobs = store.jobs()
    job_map = {job["id"]: job for job in jobs}
    a, b, c = st.columns(3)
    selected = a.selectbox("Campaign", ["all"] + list(job_map), format_func=lambda key: "All campaigns" if key == "all" else job_map[key]["name"])
    status = b.selectbox("Qualification", ["QUALIFIED", "REVIEW", "All"])
    query = c.text_input("Search businesses", placeholder="Name, domain, email or niche")
    leads = store.leads(None if selected == "all" else selected, None if status == "All" else status)
    if query:
        leads = [lead for lead in leads if query.lower() in " ".join(str(lead.get(k, "")) for k in ("company_name", "domain", "email", "industry", "country")).lower()]
    st.caption(f"{len(leads):,} matching records \u00b7 Published emails have not been verified for delivery.")
    display_leads(leads, "database")

elif page == "Settings":
    st.markdown('<div class="eyebrow">WORKSPACE CONNECTIONS</div>', unsafe_allow_html=True)
    st.title("Settings")
    st.write("Discovery and qualification work without API keys. Your existing Google Sheets and outreach connections are separate.")
    from core.settings_ui import render as render_settings
    render_settings(outreach_store,identity)
    with st.container(border=True):
        st.subheader("Free discovery engine")
        st.success("Ready. No search or model subscription is required.")
        st.write("OpenStreetMap supplies mapped businesses. Bing and DuckDuckGo add public website discovery. Python workers check and score the evidence locally.")
        st.caption("Successful searches are cached for seven days. Searches are paced; unavailable sources back off for 5–60 minutes. A session pauses after its time limit and can be resumed.")
        st.markdown("[DDGS on GitHub](https://github.com/deedy5/ddgs) · [Overpass on GitHub](https://github.com/drolbr/Overpass-API) · [OpenStreetMap attribution](https://www.openstreetmap.org/copyright)")
    with st.container(border=True):
        st.subheader("Google Sheets & outreach")
        st.caption('Update connections in Streamlit Secrets.' if config.CLOUD_MODE else 'Your existing service account and Gmail configuration are retained. Edit .env to change them.')
        st.write("Service account: " + ("Available" if config.SERVICE_ACCOUNT_JSON or Path(config.SERVICE_ACCOUNT_FILE).exists() else "Missing"))
        st.write("Gmail sender: " + ("Configured" if config.GMAIL_ADDRESS and config.GMAIL_APP_PASSWORD else "Not configured"))
        if st.button("Check Gmail & Sheet access"):
            try:
                email_sender.check_authentication()
                with Mailbox():
                    sheets.read_all("Leads", sheets.LEADS_HEADERS)
                st.success("Gmail sending/reading and Google Sheet access are available.")
            except Exception as exc:
                safe_error(exc)
        if st.button("Check Supabase cloud schema"):
            try:
                check_supabase_schema()
                st.success("Supabase is reachable and the cloud schema is installed.")
            except Exception as exc:
                safe_error(exc)
        st.caption("Discovery does not send messages. Use Outreach & CRM for sending controls, delivery history and the optional daily schedule.")
    with st.expander("How quality and volume work", expanded=True):
        st.write("Qualified means the business website supports the niche and target location, a required public contact method is present, and the fit score passes your threshold. It does not prove purchase intent.")
        st.write("A 1,000-lead target is a stopping condition, not a promised yield. Narrow markets, duplicate businesses, missing contacts, blocked pages or API limits can produce a smaller result. Every shortfall is reported with a reason.")
        st.write("The crawler checks up to six public pages per site, observes robots.txt and records blocked or JavaScript-only pages for review. It does not solve CAPTCHAs. Other independent free sources can continue when one is unavailable.")
        st.caption("Backups of your original code: .backups/before-premium-upgrade \u00b7 Lead database: data/prospect_studio.db")

elif page == "Outreach & CRM":
    from core.outreach_ui import render
    render(store)

elif page == 'Schedule':
    from core.schedule_ui import render as render_schedule
    render_schedule(outreach_store)

elif page == 'WhatsApp outreach':
    from core.messaging_ui import render as render_messaging
    render_messaging(outreach_store)

elif page == 'Social research':
    from core.social_ui import render as render_social
    render_social(store,outreach_store)
