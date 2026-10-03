"""Durable outbound ledger. A Sheet failure can never erase a successful send."""
from contextlib import contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import time
import uuid
from zoneinfo import ZoneInfo
from .config import config

TZ = ZoneInfo("Asia/Karachi")
DEFAULTS = {"enabled": False, "hour": 15, "minute": 0, "initial_limit": 20,
            "followup_limit": 10, "total_limit": 30, "min_score": 75,
            "signature_name": config.YOUR_NAME, "sender": "", "sheet_id": "",
            "sheet_name": "Leads", "auto_sync_enabled": True, "auto_send_enabled": False,
            "schedule_timezone":"Asia/Karachi", "schedule_weekdays":[0,1,2,3,4,5,6],
            "sync_interval_minutes":5,"initial_enabled":True,"followup_enabled":True,
            "auto_whatsapp_enabled":False,"whatsapp_daily_limit":10}
COUNTED = ("prepared", "sending", "uncertain", "sent")


def local_day(timestamp=None):
    return datetime.fromtimestamp(time.time() if timestamp is None else timestamp, TZ).date().isoformat()


class OutreachStore:
    def __init__(self, path=None):
        self.path = str(path or config.OUTREACH_DATABASE_PATH)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with self.db() as db:
            db.executescript("""
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS deliveries(
                id TEXT PRIMARY KEY, sender TEXT NOT NULL, sheet_id TEXT NOT NULL,
                domain TEXT NOT NULL, email TEXT NOT NULL, stage INTEGER NOT NULL,
                message_id TEXT NOT NULL UNIQUE, subject TEXT NOT NULL, body TEXT NOT NULL,
                html TEXT NOT NULL, reply_to TEXT NOT NULL, lead TEXT NOT NULL,
                state TEXT NOT NULL, created REAL NOT NULL, sent_at REAL DEFAULT 0,
                day TEXT NOT NULL, reason TEXT DEFAULT '', sheet_synced INTEGER DEFAULT 0,
                UNIQUE(sender,domain,stage));
            CREATE TABLE IF NOT EXISTS stops(
                identity TEXT PRIMARY KEY, status TEXT NOT NULL, reason TEXT NOT NULL, updated REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS activity(
                id INTEGER PRIMARY KEY, created REAL NOT NULL, message TEXT NOT NULL);
            """)
            columns = {r[1] for r in db.execute('PRAGMA table_info(deliveries)')}
            for name, definition in (('attempts', 'INTEGER NOT NULL DEFAULT 1'),
                                     ('retry_at', 'REAL NOT NULL DEFAULT 0'),
                                     ('retryable', 'INTEGER NOT NULL DEFAULT 0')):
                if name not in columns:
                    db.execute(f'ALTER TABLE deliveries ADD COLUMN {name} {definition}')

    @contextmanager
    def db(self):
        from .cloud_runtime import guard, checkpoint_outreach
        guard()
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        changed = False
        try:
            with db:
                yield db
            changed = db.total_changes > 0
        finally:
            db.close()
        if changed:
            checkpoint_outreach(self.path)

    def get(self, key, default=None):
        with self.db() as db:
            row = db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def set(self, key, value):
        with self.db() as db:
            db.execute("INSERT OR REPLACE INTO settings VALUES(?,?)", (key, json.dumps(value)))

    def settings(self):
        return {**DEFAULTS, **self.get("preferences", {})}

    def save_settings(self, values):
        prefs = {**self.settings(), **values}
        for key, maximum in (("initial_limit", 20), ("followup_limit", 10), ("total_limit", 30),
                             ("min_score", 100), ("hour", 23), ("minute", 59),
                             ("whatsapp_daily_limit",30)):
            if type(prefs[key]) is not int or not 0 <= prefs[key] <= maximum:
                raise ValueError(f"{key} must be between 0 and {maximum}.")
        for key in ('auto_sync_enabled','auto_send_enabled','initial_enabled','followup_enabled','auto_whatsapp_enabled'):
            if type(prefs[key]) is not bool:
                raise ValueError(f'{key} must be true or false.')
        interval=prefs['sync_interval_minutes']
        if type(interval) is not int or not 1<=interval<=60:
            raise ValueError('Sheet sync interval must be 1–60 minutes.')
        days=prefs['schedule_weekdays']
        if not isinstance(days,list) or any(type(day) is not int or day not in range(7) for day in days):
            raise ValueError('Choose valid schedule weekdays.')
        if (prefs['auto_send_enabled'] or prefs['auto_whatsapp_enabled']) and not days:
            raise ValueError('Choose at least one sending day.')
        try:
            ZoneInfo(prefs['schedule_timezone'])
        except (KeyError,ValueError,TypeError):
            raise ValueError('Choose a valid IANA timezone.') from None
        prefs["signature_name"] = str(prefs["signature_name"]).strip()[:100]
        if not prefs["signature_name"]:
            raise ValueError("Enter a signature name.")
        prefs['sheet_name'] = str(prefs['sheet_name']).strip()
        if not prefs['sheet_name']:
            raise ValueError('Choose the outreach worksheet name.')
        # Enabling is an explicit dashboard action and binds the intended account and Sheet.
        prefs["sender"], prefs["sheet_id"] = config.GMAIL_ADDRESS.lower(), config.GOOGLE_SHEET_ID
        self.set("preferences", prefs)

    def event(self, message):
        with self.db() as db:
            db.execute("INSERT INTO activity(created,message) VALUES(?,?)", (time.time(), message[:1200]))

    def events(self, limit=30):
        with self.db() as db:
            return [dict(r) for r in db.execute("SELECT * FROM activity ORDER BY id DESC LIMIT ?", (limit,))]

    def deliveries(self, domain=None):
        with self.db() as db:
            if domain:
                rows = db.execute("SELECT * FROM deliveries WHERE sender=? AND domain=? ORDER BY stage",
                                  (config.GMAIL_ADDRESS.lower(), domain))
            else:
                rows = db.execute("SELECT * FROM deliveries WHERE sender=? ORDER BY created DESC", (config.GMAIL_ADDRESS.lower(),))
            return [dict(r) for r in rows]

    def stop(self, domain, email, status, reason):
        with self.db() as db:
            for identity in (domain, email.lower()):
                if identity:
                    db.execute("INSERT OR REPLACE INTO stops VALUES(?,?,?,?)", (identity, status, reason, time.time()))

    def stopped(self, domain, email):
        with self.db() as db:
            row = db.execute("SELECT status,reason FROM stops WHERE identity IN (?,?) LIMIT 1", (domain, email.lower())).fetchone()
        return dict(row) if row else None

    def reserve(self, lead, stage, draft, reply_to="", now=None):
        now = time.time() if now is None else now
        identity = uuid.uuid4().hex
        message_id = f"<prospect-{identity}@{config.GMAIL_ADDRESS.rsplit('@',1)[-1]}>"
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            # Email aliases on another website must not create a second sequence.
            if db.execute("SELECT 1 FROM deliveries WHERE sender=? AND email=? AND domain<>? AND state IN ('prepared','sending','sent','uncertain')",
                          (config.GMAIL_ADDRESS.lower(), lead['email'].lower(), lead['domain'])).fetchone():
                return None
            existing = db.execute("SELECT * FROM deliveries WHERE sender=? AND domain=? AND stage=?",
                                  (config.GMAIL_ADDRESS.lower(), lead['domain'], stage)).fetchone()
            if existing:
                if (existing['state'] != 'failed' or not existing['retryable'] or
                        existing['attempts'] >= 3 or existing['retry_at'] > now or
                        existing['email'] != lead['email'].lower()):
                    return None
                identity = existing['id']
                db.execute("UPDATE deliveries SET state='prepared',attempts=attempts+1,day=?,retryable=0 WHERE id=?",
                           (local_day(now), identity))
            else:
                db.execute("""INSERT INTO deliveries(id,sender,sheet_id,domain,email,stage,message_id,subject,body,html,reply_to,lead,state,created,day)
                          VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                       (identity, config.GMAIL_ADDRESS.lower(), config.GOOGLE_SHEET_ID, lead['domain'], lead['email'].lower(),
                        stage, message_id, draft['subject'], draft['body'], draft['html'], reply_to, json.dumps(lead), 'prepared', now, local_day(now)))
        return next(r for r in self.deliveries(lead['domain']) if r['id'] == identity)

    def update(self, delivery_id, state, reason="", sent_at=None):
        with self.db() as db:
            db.execute("UPDATE deliveries SET state=?,reason=?,sent_at=CASE WHEN ? IS NULL THEN sent_at ELSE ? END WHERE id=?",
                       (state, reason[:600], sent_at, sent_at, delivery_id))
            if sent_at is not None:
                db.execute('UPDATE deliveries SET day=? WHERE id=?',(local_day(sent_at),delivery_id))

    def mark_synced(self, delivery_id):
        with self.db() as db:
            db.execute("UPDATE deliveries SET sheet_synced=1 WHERE id=?", (delivery_id,))

    def record_failure(self, delivery_id, result, now=None):
        now = time.time() if now is None else now
        self.update(delivery_id, result['state'], result['reason'])
        with self.db() as db:
            row = db.execute('SELECT attempts FROM deliveries WHERE id=?', (delivery_id,)).fetchone()
            retryable = result['state'] == 'failed' and bool(result.get('retryable'))
            delay = min(3600, 300 * 2 ** (row['attempts'] - 1))
            db.execute('UPDATE deliveries SET retryable=?,retry_at=? WHERE id=?',
                       (int(retryable), now + delay if retryable else 0, delivery_id))

    def usage(self, sent_mail, now=None):
        day = local_day(now)
        own = [r for r in self.deliveries() if r['day'] == day and r['state'] in COUNTED]
        known_ids = {r['message_id'] for r in own}
        external = {m['id'] for m in sent_mail if m['message_id'] not in known_ids}
        # Other tools share this Gmail account. Unknown sends conservatively consume both type allowances.
        return {"initial": sum(r['stage'] == 0 for r in own) + len(external),
                "followup": sum(r['stage'] > 0 for r in own) + len(external),
                "total": len(own) + len(external), "other_sent": len(external),
                "ledger_initial": sum(r['stage'] == 0 for r in own),
                "ledger_followup": sum(r['stage'] > 0 for r in own),
                "ledger_total": len(own)}
