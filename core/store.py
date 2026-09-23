"""Transactional run checkpoints and a local, domain-deduplicated lead database."""
from contextlib import contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import time
import uuid
import hashlib
from .config import config


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Store:
    def __init__(self, path=None):
        self.path = str(path or config.DATABASE_PATH)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with self.db() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY, name TEXT NOT NULL, settings TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'ready', reason TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                    heartbeat REAL DEFAULT 0, owner TEXT DEFAULT '', stop_requested INTEGER DEFAULT 0,
                    search_calls INTEGER DEFAULT 0, ai_calls INTEGER DEFAULT 0,
                    duplicates INTEGER DEFAULT 0, source_errors INTEGER DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS queries (
                    id INTEGER PRIMARY KEY, job_id TEXT NOT NULL, data TEXT NOT NULL,
                    page INTEGER DEFAULT 1, status TEXT DEFAULT 'pending', attempts INTEGER DEFAULT 0
                );
                CREATE INDEX IF NOT EXISTS query_queue ON queries(job_id,status,page,id);
                CREATE TABLE IF NOT EXISTS candidates (
                    id INTEGER PRIMARY KEY, job_id TEXT NOT NULL, domain TEXT NOT NULL,
                    data TEXT NOT NULL, status TEXT DEFAULT 'pending', UNIQUE(job_id,domain)
                );
                CREATE INDEX IF NOT EXISTS candidate_queue ON candidates(job_id,status,id);
                CREATE TABLE IF NOT EXISTS leads (
                    domain TEXT PRIMARY KEY, job_id TEXT NOT NULL, email TEXT NOT NULL,
                    qualification_status TEXT NOT NULL, data TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS leads_job ON leads(job_id,qualification_status);
                CREATE INDEX IF NOT EXISTS leads_email ON leads(email);
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY, job_id TEXT NOT NULL, created_at TEXT NOT NULL,
                    level TEXT NOT NULL, message TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS source_state (
                    job_id TEXT NOT NULL, source TEXT NOT NULL, blocked_until REAL DEFAULT 0,
                    failures INTEGER DEFAULT 0, reason TEXT DEFAULT '', PRIMARY KEY(job_id,source)
                );
                CREATE TABLE IF NOT EXISTS search_cache (
                    key TEXT PRIMARY KEY, created_at REAL NOT NULL, data TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS source_pacing (
                    source TEXT PRIMARY KEY, next_request_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS source_daily_usage (
                    source TEXT NOT NULL, day TEXT NOT NULL, requests INTEGER NOT NULL,
                    PRIMARY KEY(source,day)
                );
            """)
            columns = {row[1] for row in db.execute("PRAGMA table_info(queries)")}
            if "source" not in columns:
                db.execute("ALTER TABLE queries ADD COLUMN source TEXT NOT NULL DEFAULT ''")

    @contextmanager
    def db(self):
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def create_job(self, settings, queries):
        job_id = uuid.uuid4().hex[:12]
        with self.db() as db:
            db.execute("INSERT INTO jobs(id,name,settings,created_at,updated_at) VALUES(?,?,?,?,?)",
                       (job_id, settings["name"], json.dumps(settings), now_iso(), now_iso()))
            db.executemany("INSERT INTO queries(job_id,data,source) VALUES(?,?,?)",
                           [(job_id, json.dumps(query), query.get("source_kind", "")) for query in queries])
        self.event(job_id, f"Campaign created: {len(queries):,} search combinations.")
        return job_id

    def job(self, job_id):
        with self.db() as db:
            row = db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if not row:
            raise ValueError("Campaign not found.")
        job = dict(row)
        job["settings"] = json.loads(job["settings"])
        return job

    def jobs(self):
        with self.db() as db:
            return [dict(row) for row in db.execute("SELECT id,name,status,created_at FROM jobs ORDER BY created_at DESC, rowid DESC")]

    def event(self, job_id, message, level="info"):
        with self.db() as db:
            db.execute("INSERT INTO events(job_id,created_at,level,message) VALUES(?,?,?,?)",
                       (job_id, now_iso(), level, message[:1200]))

    def events(self, job_id, limit=25):
        with self.db() as db:
            return [dict(row) for row in db.execute(
                "SELECT created_at,level,message FROM events WHERE job_id=? ORDER BY id DESC LIMIT ?", (job_id, limit))]

    def reserve_worker(self, job_id):
        """Only one live worker may use this DB; stale leases can be recovered."""
        token = uuid.uuid4().hex
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT 1 FROM jobs WHERE status IN ('running','starting') AND heartbeat>?",
                          (time.time() - 90,)).fetchone():
                raise ValueError("A campaign is already running. Pause it before starting another.")
            job = db.execute("SELECT status FROM jobs WHERE id=?", (job_id,)).fetchone()
            if not job or job["status"] == "complete":
                raise ValueError("This campaign is already complete or does not exist.")
            db.execute("UPDATE jobs SET status='interrupted',reason='Worker heartbeat expired',owner='' WHERE status IN ('running','starting')")
            db.execute("UPDATE queries SET status='pending' WHERE job_id=? AND status='in_progress'", (job_id,))
            db.execute("UPDATE candidates SET status='pending' WHERE job_id=? AND status='processing'", (job_id,))
            db.execute("UPDATE jobs SET status='starting',owner=?,heartbeat=?,stop_requested=0,reason='',updated_at=? WHERE id=?",
                       (token, time.time(), now_iso(), job_id))
        return token

    def activate(self, job_id, token):
        with self.db() as db:
            return db.execute("UPDATE jobs SET status='running',heartbeat=? WHERE id=? AND owner=? AND status='starting'",
                              (time.time(), job_id, token)).rowcount == 1

    def heartbeat(self, job_id, token):
        with self.db() as db:
            return db.execute("UPDATE jobs SET heartbeat=?,updated_at=? WHERE id=? AND owner=? AND status IN ('running','starting')",
                              (time.time(), now_iso(), job_id, token)).rowcount == 1

    def finish(self, job_id, token, status, reason):
        with self.db() as db:
            changed = db.execute("UPDATE jobs SET status=?,reason=?,owner='',updated_at=? WHERE id=? AND owner=?",
                                 (status, reason, now_iso(), job_id, token)).rowcount
            if changed:
                db.execute("UPDATE candidates SET status='pending' WHERE job_id=? AND status='processing'", (job_id,))
                db.execute("UPDATE queries SET status='pending' WHERE job_id=? AND status='in_progress'", (job_id,))
        if changed:
            self.event(job_id, reason, "info" if status == "complete" else "warning")

    def request_pause(self, job_id):
        with self.db() as db:
            db.execute("UPDATE jobs SET stop_requested=1 WHERE id=?", (job_id,))

    def revise_limits(self, job_id, search_limit, candidate_limit, provider, ai_limit=None):
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            if row["status"] in ("running", "starting") and row["heartbeat"] > time.time() - 90:
                raise ValueError("Pause the campaign before changing limits.")
            settings = json.loads(row["settings"])
            old_provider = settings["provider"]
            settings.update(max_search_calls=int(search_limit), max_candidates=int(candidate_limit), provider=provider)
            if provider == "free":
                settings["use_ai"] = False
                settings["max_ai_calls"] = 0
            if ai_limit is not None:
                settings["max_ai_calls"] = int(ai_limit)
            from .campaigns import validate_settings
            validate_settings(settings)
            db.execute("UPDATE jobs SET settings=? WHERE id=?", (json.dumps(settings), job_id))
            db.execute("UPDATE queries SET attempts=0 WHERE job_id=? AND status='pending'", (job_id,))
            if provider != old_provider:
                from .markets import plan_queries
                db.execute("UPDATE queries SET status='superseded' WHERE job_id=? AND status='pending'", (job_id,))
                db.executemany("INSERT INTO queries(job_id,data,source) VALUES(?,?,?)",
                               [(job_id, json.dumps(query), query.get("source_kind", "")) for query in plan_queries(settings)])

    def next_query(self, job_id):
        with self.db() as db:
            row = db.execute("""SELECT q.* FROM queries q LEFT JOIN source_state s
                ON q.job_id=s.job_id AND q.source=s.source
                WHERE q.job_id=? AND q.status='pending' AND COALESCE(s.blocked_until,0)<=?
                ORDER BY q.page,q.id LIMIT 1""", (job_id, time.time())).fetchone()
        return {**dict(row), "data": json.loads(row["data"])} if row else None

    def replace_ready_plan(self, job_id, settings, queries):
        """Convert a never-started draft without discarding collected results."""
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            if not row or row["status"] != "ready" or row["search_calls"]:
                raise ValueError("Only an unstarted draft can be replanned.")
            db.execute("DELETE FROM queries WHERE job_id=?", (job_id,))
            db.executemany("INSERT INTO queries(job_id,data,source) VALUES(?,?,?)",
                           [(job_id, json.dumps(query), query.get("source_kind", "")) for query in queries])
            db.execute("UPDATE jobs SET settings=?,updated_at=? WHERE id=?", (json.dumps(settings), now_iso(), job_id))

    def cache_key(self, query, page):
        payload = json.dumps({"query": query, "page": page, "version": 1}, sort_keys=True)
        return hashlib.sha256(payload.encode()).hexdigest()

    def cached_search(self, query, page):
        with self.db() as db:
            row = db.execute("SELECT data FROM search_cache WHERE key=? AND created_at>?",
                             (self.cache_key(query, page), time.time() - 7 * 86400)).fetchone()
        if row:
            from .providers import SearchPage
            data = json.loads(row[0])
            return SearchPage(data["items"], data["has_more"])

    def cache_search(self, query, page_number, page):
        with self.db() as db:
            db.execute("INSERT OR REPLACE INTO search_cache VALUES(?,?,?)",
                       (self.cache_key(query, page_number), time.time(), json.dumps({"items": page.items, "has_more": page.has_more})))

    def seconds_until_source(self, source):
        with self.db() as db:
            row = db.execute("SELECT next_request_at FROM source_pacing WHERE source=?", (source,)).fetchone()
        return max(0, row[0] - time.time()) if row else 0

    def pace_source(self, source, interval):
        with self.db() as db:
            db.execute("INSERT OR REPLACE INTO source_pacing VALUES(?,?)", (source, time.time() + interval))

    def defer_source(self, job_id, source, reason):
        with self.db() as db:
            row = db.execute("SELECT failures FROM source_state WHERE job_id=? AND source=?", (job_id, source)).fetchone()
            failures = (row[0] if row else 0) + 1
            delay = min(300 * 2 ** min(failures - 1, 4), 3600)
            db.execute("INSERT OR REPLACE INTO source_state VALUES(?,?,?,?,?)", (job_id, source, time.time() + delay, failures, reason))
        return delay

    def source_recovered(self, job_id, source):
        with self.db() as db:
            db.execute("DELETE FROM source_state WHERE job_id=? AND source=?", (job_id, source))

    def reserve_open_data_allowance(self):
        # <=100 x 8 MB bounded downloads per UTC day stays below the public 1 GB guideline.
        day = now_iso()[:10]
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("INSERT OR IGNORE INTO source_daily_usage VALUES('osm',?,0)", (day,))
            return bool(db.execute("UPDATE source_daily_usage SET requests=requests+1 WHERE source='osm' AND day=? AND requests<100", (day,)).rowcount)

    def source_health(self, job_id):
        with self.db() as db:
            return [dict(row) for row in db.execute("SELECT source,blocked_until,failures,reason FROM source_state WHERE job_id=?", (job_id,))]

    def reserve_search_call(self, job_id, query_id, token, limit):
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            ok = db.execute("UPDATE jobs SET search_calls=search_calls+1 WHERE id=? AND owner=? AND search_calls<? AND stop_requested=0",
                            (job_id, token, limit)).rowcount
            if ok:
                db.execute("UPDATE queries SET status='in_progress',attempts=attempts+1 WHERE id=?", (query_id,))
            return bool(ok)

    def source_failed(self, job_id, query_id, token):
        with self.db() as db:
            if db.execute("SELECT 1 FROM jobs WHERE id=? AND owner=?", (job_id, token)).fetchone():
                db.execute("UPDATE queries SET status='pending' WHERE id=?", (query_id,))
                db.execute("UPDATE jobs SET source_errors=source_errors+1 WHERE id=?", (job_id,))

    def save_search(self, job_id, query, page, token, max_pages, candidate_limit):
        """Checkpoint new candidates and the search cursor in the same transaction."""
        added = duplicates = 0
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            if not db.execute("SELECT 1 FROM jobs WHERE id=? AND owner=?", (job_id, token)).fetchone():
                return 0
            count = db.execute("SELECT COUNT(*) FROM candidates WHERE job_id=?", (job_id,)).fetchone()[0]
            for item in page.items:
                if db.execute("SELECT 1 FROM leads WHERE domain=?", (item["domain"],)).fetchone():
                    duplicates += 1
                    continue
                inserted = db.execute("INSERT OR IGNORE INTO candidates(job_id,domain,data) VALUES(?,?,?)",
                                      (job_id, item["domain"], json.dumps(item))).rowcount
                added += inserted
                duplicates += not inserted
            more = page.has_more and query["page"] < max_pages
            db.execute("UPDATE queries SET page=?,status=?,attempts=0 WHERE id=?",
                       (query["page"] + 1 if more else query["page"], "pending" if more else "complete", query["id"]))
            db.execute("UPDATE jobs SET duplicates=duplicates+? WHERE id=?", (duplicates, job_id))
        return added

    def candidate_batch(self, job_id, limit):
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            rows = db.execute("SELECT * FROM candidates WHERE job_id=? AND status='pending' ORDER BY id LIMIT ?", (job_id, limit)).fetchall()
            db.executemany("UPDATE candidates SET status='processing' WHERE id=?", [(row["id"],) for row in rows])
        return [{**dict(row), "data": json.loads(row["data"])} for row in rows]

    def save_lead(self, job_id, candidate_id, lead, token):
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            if not db.execute("SELECT 1 FROM jobs WHERE id=? AND owner=?", (job_id, token)).fetchone():
                return False
            if lead.get("email") and lead["qualification_status"] == "QUALIFIED":
                if db.execute("SELECT 1 FROM leads WHERE email=? AND qualification_status='QUALIFIED'", (lead["email"],)).fetchone():
                    lead["qualification_status"] = lead["status"] = "REVIEW"
                    lead["fit"] = False
                    lead["qualification_reasons"].append("Email already belongs to a qualified lead in this database.")
            inserted = db.execute("INSERT OR IGNORE INTO leads VALUES(?,?,?,?,?,?)",
                                  (lead["domain"], job_id, lead.get("email", ""), lead["qualification_status"], json.dumps(lead), now_iso())).rowcount
            db.execute("UPDATE candidates SET status='done' WHERE id=?", (candidate_id,))
        return bool(inserted)

    def reserve_ai_call(self, job_id, token, limit):
        with self.db() as db:
            return bool(db.execute("UPDATE jobs SET ai_calls=ai_calls+1 WHERE id=? AND owner=? AND ai_calls<?",
                                   (job_id, token, limit)).rowcount)

    def stats(self, job_id=None):
        clause, args = (" WHERE job_id=?", (job_id,)) if job_id else ("", ())
        with self.db() as db:
            counts = {row[0]: row[1] for row in db.execute("SELECT qualification_status,COUNT(*) FROM leads" + clause + " GROUP BY qualification_status", args)}
            stats = {"qualified": counts.get("QUALIFIED", 0), "review": counts.get("REVIEW", 0), "processed": sum(counts.values())}
            if job_id:
                stats["candidates"] = db.execute("SELECT COUNT(*) FROM candidates WHERE job_id=?", args).fetchone()[0]
                stats["pending"] = db.execute("SELECT COUNT(*) FROM candidates WHERE job_id=? AND status!='done'", args).fetchone()[0]
                stats["queries_left"] = db.execute("SELECT COUNT(*) FROM queries WHERE job_id=? AND status='pending'", args).fetchone()[0]
                stats["queries_total"] = db.execute("SELECT COUNT(*) FROM queries WHERE job_id=?", args).fetchone()[0]
        return stats

    def leads(self, job_id=None, status=None):
        clauses, args = [], []
        if job_id:
            clauses.append("job_id=?")
            args.append(job_id)
        if status:
            clauses.append("qualification_status=?")
            args.append(status)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        with self.db() as db:
            return [json.loads(row[0]) for row in db.execute("SELECT data FROM leads" + where + " ORDER BY created_at DESC, domain", args)]
