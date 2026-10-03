"""Persistent campaign runner used by the UI and the command-line worker."""
from concurrent.futures import ThreadPoolExecutor, as_completed
import os
import subprocess
import sys
import threading
import time
from . import ai
from .config import BASE_DIR, LOG_DIR, config
from .markets import plan_queries
from .providers import search_page, ProviderError
from .qualification import qualify
from .store import Store
from .websites import enrich_website


class AIUnavailable(Exception):
    pass


def validate_settings(settings):
    if not str(settings.get("name", "")).strip():
        raise ValueError("Give this campaign a name.")
    if not settings.get("niches") or not settings.get("locations"):
        raise ValueError("Select at least one niche and one city.")
    if settings.get("provider") not in {"free", "bing", "serpapi"}:
        raise ValueError("Choose free discovery or Bing.")
    ranges = {"target": (1, 100000), "min_score": (0, 100), "max_search_calls": (1, 100000),
              "max_candidates": (1, 200000), "workers": (1, 16), "max_pages": (1, 10),
              "website_pages": (1, 6), "max_ai_calls": (0, 100000)}
    for key, (low, high) in ranges.items():
        value = settings.get(key)
        if type(value) is not int or not low <= value <= high:
            raise ValueError(f"{key} must be between {low} and {high}.")
    if settings["max_candidates"] < settings["target"]:
        raise ValueError("The candidate limit must be at least as large as the qualified lead target.")
    if settings.get("provider") == "free" and settings.get("use_ai"):
        raise ValueError("Free discovery uses local evidence scoring; paid AI review is disabled.")
    if len(settings["niches"]) > 30 or len(settings["locations"]) > 500:
        raise ValueError("Limit a campaign to 30 niches and 500 cities.")


def create_campaign(settings, store=None):
    validate_settings(settings)
    return (store or Store()).create_job(settings, plan_queries(settings))


def launch(job_id, store=None):
    store = store or Store()
    job = store.job(job_id)
    validate_settings(job["settings"])
    if job["settings"]["provider"] == "serpapi" and not config.SERPAPI_API_KEY:
        raise ValueError("Save your SerpApi key in Settings first. You can also use Bing preview.")
    if job["settings"].get("use_ai") and not config.OPENROUTER_API_KEY:
        raise ValueError("Save your OpenRouter key before enabling AI review.")
    token = store.reserve_worker(job_id)
    if config.CLOUD_MODE:
        thread = threading.Thread(target=run_job,args=(job_id,),kwargs={'store':store,'token':token},daemon=True)
        thread.start()
        return thread.ident
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        log_path = LOG_DIR / "worker.log"
        with log_path.open("a", encoding="utf-8") as log:
            process = subprocess.Popen([sys.executable, str(BASE_DIR / "worker.py"), "--job", job_id,
                              "--token", token, "--db", store.path], cwd=str(BASE_DIR),
                             stdout=log, stderr=log, stdin=subprocess.DEVNULL,
                             creationflags=flags, start_new_session=os.name != "nt")
        threading.Thread(target=process.wait, daemon=True).start()
        return process.pid
    except Exception:
        store.finish(job_id, token, "failed", "Could not start the worker. Check the Python installation.")
        raise


def _process(candidate, settings, store, job_id, token):
    enrichment = enrich_website(candidate["website"], max_pages=settings["website_pages"])
    lead = qualify(candidate, enrichment, settings)
    if settings.get("use_ai") and lead["qualification_status"] == "QUALIFIED":
        if not store.reserve_ai_call(job_id, token, settings["max_ai_calls"]):
            raise AIUnavailable("AI request limit reached. Increase it in a new campaign or continue this campaign with a larger AI allowance.")
        assessment = ai.review_evidence(lead, enrichment.get("text", ""), settings["target_service"])
        if assessment.get("error"):
            raise AIUnavailable("AI review is unavailable. Check the OpenRouter model, rate limits and credits, then resume.")
        lead["ai_status"] = "reviewed"
        lead["ai_score"] = assessment["lead_score"]
        lead["lead_score"] = min(lead["rules_score"], assessment["lead_score"])
        lead["ai_reason"] = assessment["reason"]
        lead["recommended_service"] = assessment["recommended_service"]
        if not assessment["ideal_customer_fit"] or lead["lead_score"] < settings["min_score"]:
            lead["status"] = lead["qualification_status"] = "REVIEW"
            lead["fit"] = False
            lead["qualification_reasons"].append("AI review did not meet the campaign threshold: " + assessment["reason"])
    return lead


def run_job(job_id, store=None, token=None, search=search_page, process=_process):
    store = store or Store()
    token = token or store.reserve_worker(job_id)
    if not store.activate(job_id, token):
        return
    settings = store.job(job_id)["settings"]
    stopped = threading.Event()

    def pulse():
        while not stopped.wait(10):
            if not store.heartbeat(job_id, token):
                return
    heartbeat = threading.Thread(target=pulse, daemon=True)
    heartbeat.start()
    started = time.monotonic()
    waiting_logged = False
    store.event(job_id, "Worker started. Each completed result is saved immediately.")
    try:
        with ThreadPoolExecutor(max_workers=settings["workers"]) as pool:
            while True:
                job, stats = store.job(job_id), store.stats(job_id)
                if job["owner"] != token:
                    return
                if job["stop_requested"]:
                    store.finish(job_id, token, "paused", "Paused by you. Resume to continue from saved progress.")
                    return
                if time.monotonic() - started >= settings.get("max_runtime_hours", 12) * 3600:
                    store.finish(job_id, token, "time_limit", "Session time limit reached. Resume to continue the saved queue.")
                    return
                if stats["qualified"] >= settings["target"]:
                    store.finish(job_id, token, "complete", f'Target achieved: {stats["qualified"]:,} new qualified leads.')
                    return
                if stats["processed"] >= settings["max_candidates"]:
                    store.finish(job_id, token, "candidate_limit", "Candidate review limit reached before the target. Increase the limit to continue.")
                    return
                batch = store.candidate_batch(job_id, min(settings["workers"], settings["target"] - stats["qualified"], settings["max_candidates"] - stats["processed"]))
                if batch:
                    pending = {pool.submit(process, item["data"], settings, store, job_id, token): item for item in batch}
                    ai_error = None
                    for future in as_completed(pending):
                        item = pending[future]
                        try:
                            lead = future.result()
                        except AIUnavailable as exc:
                            ai_error = str(exc)
                            continue
                        # Unexpected implementation failures pause the run instead of silently losing candidates.
                        store.save_lead(job_id, item["id"], lead, token)
                    if ai_error:
                        store.finish(job_id, token, "ai_unavailable", ai_error)
                        return
                    continue
                query = store.next_query(job_id)
                if not query:
                    if job["search_calls"] >= settings["max_search_calls"] and stats["queries_left"]:
                        store.finish(job_id, token, "search_limit", "Discovery request limit reached. Raise the limit to resume saved work.")
                        return
                    if settings["provider"] == "free" and stats["queries_left"]:
                        if not waiting_logged:
                            store.event(job_id, "Free sources are cooling down. The worker will retry automatically; you can pause anytime.", "warning")
                            waiting_logged = True
                        time.sleep(1)
                        continue
                    store.finish(job_id, token, "exhausted", f'Search plan exhausted with {stats["qualified"]:,}/{settings["target"]:,} qualified leads. Create a broader campaign to find more.')
                    return
                waiting_logged = False
                free = settings["provider"] == "free"
                cached = store.cached_search(query["data"], query["page"]) if free else None
                if cached is not None:
                    count = store.save_search(job_id, query, cached, token, settings["max_pages"], settings["max_candidates"])
                    store.event(job_id, f'Cached {query["data"]["keyword"]} / {query["data"]["city"]}: {count} new candidates.')
                    continue
                if job["search_calls"] >= settings["max_search_calls"]:
                    store.finish(job_id, token, "search_limit", "Search request limit reached. All results are saved; raise the limit to resume.")
                    return
                source = query["data"].get("source_kind", settings["provider"])
                if free and store.seconds_until_source(source) > 0:
                    time.sleep(min(store.seconds_until_source(source), 1))
                    continue
                if free and source == "osm" and not store.reserve_open_data_allowance():
                    store.defer_source(job_id, source, "Daily open-data request allowance reached. Web discovery can continue; map discovery resets tomorrow UTC.")
                    continue
                if not store.reserve_search_call(job_id, query["id"], token, settings["max_search_calls"]):
                    continue
                if free:
                    store.pace_source(source, 15 if source == "osm" else 8)
                try:
                    page = search(settings["provider"], query["data"], query["page"])
                    if free:
                        store.cache_search(query["data"], query["page"], page)
                        store.source_recovered(job_id, source)
                    count = store.save_search(job_id, query, page, token, settings["max_pages"], settings["max_candidates"])
                    store.event(job_id, f'{query["data"]["keyword"]} / {query["data"]["city"]} / page {query["page"]}: {count} new candidates.')
                except ProviderError as exc:
                    store.source_failed(job_id, query["id"], token)
                    store.event(job_id, str(exc), "warning")
                    if free:
                        delay = store.defer_source(job_id, source, str(exc))
                        store.event(job_id, f"{source} cooling down for {delay // 60} minutes. Other free sources remain available.", "warning")
                        continue
                    if exc.retryable and query["attempts"] < 2:
                        time.sleep(min(2 ** (query["attempts"] + 1), 8))
                        continue
                    store.finish(job_id, token, "source_blocked", str(exc))
                    return
                time.sleep(2.0 if settings["provider"] == "bing" else 0.4)
    except Exception as exc:
        # Exception text may contain keys or URLs. Persist only the exception type.
        store.finish(job_id, token, "failed", f"Worker stopped ({type(exc).__name__}). Progress is saved; review the configuration and resume.")
        raise
    finally:
        stopped.set()
        heartbeat.join(timeout=1)
