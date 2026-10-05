"""Social research uses separate tables within the backed-up campaign database."""
import json
import time
import uuid
from .store import Store
from .social_research import utc_now, validate_settings


class SocialStore:
    def __init__(self, path=None):
        self.base = Store(path)
        self.path = self.base.path
        with self.base.db() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS social_jobs(
                    id TEXT PRIMARY KEY,name TEXT NOT NULL,settings TEXT NOT NULL,
                    status TEXT NOT NULL,reason TEXT NOT NULL DEFAULT '',created_at TEXT NOT NULL,
                    owner TEXT NOT NULL DEFAULT '',heartbeat REAL NOT NULL DEFAULT 0,
                    stop_requested INTEGER NOT NULL DEFAULT 0,requests INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS social_queries(
                    id INTEGER PRIMARY KEY,job_id TEXT NOT NULL,data TEXT NOT NULL,
                    page INTEGER NOT NULL DEFAULT 1,done INTEGER NOT NULL DEFAULT 0,
                    retry_at REAL NOT NULL DEFAULT 0);
                CREATE INDEX IF NOT EXISTS social_queue ON social_queries(job_id,done,retry_at,id);
                CREATE TABLE IF NOT EXISTS social_posts(
                    url TEXT PRIMARY KEY,data TEXT NOT NULL,review_status TEXT NOT NULL DEFAULT 'Needs review');
                CREATE TABLE IF NOT EXISTS social_job_posts(
                    job_id TEXT NOT NULL,url TEXT NOT NULL,data TEXT NOT NULL,PRIMARY KEY(job_id,url));
            ''')

    def checkpoint(self):
        from .cloud_runtime import checkpoint_campaign
        checkpoint_campaign()

    def create(self, settings, queries):
        settings = validate_settings(settings)
        job_id = uuid.uuid4().hex[:12]
        with self.base.db() as db:
            db.execute('INSERT INTO social_jobs(id,name,settings,status,created_at) VALUES(?,?,?,?,?)',
                       (job_id, settings['name'], json.dumps(settings), 'ready', utc_now()))
            db.executemany('INSERT INTO social_queries(job_id,data) VALUES(?,?)', [(job_id,json.dumps(q)) for q in queries])
        self.checkpoint()
        return job_id

    def jobs(self):
        with self.base.db() as db:
            return [dict(r) for r in db.execute('SELECT id,name,status FROM social_jobs ORDER BY created_at DESC,rowid DESC')]

    def job(self, job_id):
        with self.base.db() as db:
            row = db.execute('SELECT * FROM social_jobs WHERE id=?', (job_id,)).fetchone()
        if not row:
            raise ValueError('Research not found.')
        job = dict(row)
        job['settings'] = json.loads(job['settings'])
        return job

    def count(self, job_id):
        with self.base.db() as db:
            return db.execute('SELECT count(*) FROM social_job_posts WHERE job_id=?', (job_id,)).fetchone()[0]

    def records(self, job_id):
        with self.base.db() as db:
            rows = db.execute('''SELECT j.data,p.review_status FROM social_posts p JOIN social_job_posts j ON j.url=p.url
                                 WHERE j.job_id=? ORDER BY p.rowid DESC''', (job_id,)).fetchall()
        return [{**json.loads(r['data']), 'review_status': r['review_status']} for r in rows]

    def save_records(self, job_id, records, token=None, query=None, more=False, cursor=''):
        added = 0
        with self.base.db() as db:
            db.execute('BEGIN IMMEDIATE')
            job = db.execute('SELECT * FROM social_jobs WHERE id=?', (job_id,)).fetchone()
            if not job or token is not None and job['owner'] != token:
                return 0
            settings = json.loads(job['settings'])
            count = db.execute('SELECT count(*) FROM social_job_posts WHERE job_id=?', (job_id,)).fetchone()[0]
            page_consumed = True
            for record in records:
                if not record.get('relevant'):
                    continue
                if db.execute('SELECT 1 FROM social_job_posts WHERE job_id=? AND url=?',(job_id,record['url'])).fetchone():
                    continue
                if count + added >= settings['target']:
                    page_consumed = False
                    break
                db.execute('INSERT OR IGNORE INTO social_posts(url,data) VALUES(?,?)', (record['url'], json.dumps(record)))
                added += db.execute('INSERT OR IGNORE INTO social_job_posts VALUES(?,?,?)', (job_id, record['url'],json.dumps(record))).rowcount
            if query and page_consumed:
                has_more = more and query['page'] < settings['max_pages']
                if cursor:
                    db.execute('UPDATE social_queries SET data=? WHERE id=? AND job_id=?',
                               (json.dumps({**query['data'],'before':cursor}),query['id'],job_id))
                db.execute('UPDATE social_queries SET page=?,done=?,retry_at=0 WHERE id=? AND job_id=?',
                           (query['page'] + int(has_more), int(not has_more), query['id'], job_id))
        self.checkpoint()
        return added

    def review(self, url, status):
        if status not in ('Needs review', 'Relevant', 'Dismissed'):
            raise ValueError('Choose a valid review status.')
        with self.base.db() as db:
            db.execute('UPDATE social_posts SET review_status=? WHERE url=?', (status,url))
        self.checkpoint()

    def reserve(self, job_id):
        token = uuid.uuid4().hex
        with self.base.db() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute("SELECT 1 FROM social_jobs WHERE status='running' AND heartbeat>?", (time.time()-90,)).fetchone():
                raise ValueError('Social research is already running. Pause it before starting another run.')
            if not db.execute('SELECT 1 FROM social_jobs WHERE id=?', (job_id,)).fetchone():
                raise ValueError('Research not found.')
            db.execute("UPDATE social_jobs SET status='paused',owner='',reason='Worker stopped; resume saved searches.' WHERE status='running'")
            db.execute("UPDATE social_jobs SET status='running',owner=?,heartbeat=?,stop_requested=0,reason='' WHERE id=?", (token,time.time(),job_id))
        self.checkpoint()
        return token

    def pulse(self, job_id, token):
        with self.base.db() as db:
            return bool(db.execute("UPDATE social_jobs SET heartbeat=? WHERE id=? AND owner=? AND status='running'", (time.time(),job_id,token)).rowcount)

    def pause(self, job_id):
        with self.base.db() as db:
            db.execute('UPDATE social_jobs SET stop_requested=1 WHERE id=?', (job_id,))
        self.checkpoint()

    def finish(self, job_id, token, status, reason):
        with self.base.db() as db:
            db.execute('UPDATE social_jobs SET status=?,reason=?,owner=? WHERE id=? AND owner=?', (status,reason,'',job_id,token))
        self.checkpoint()

    def next_query(self, job_id):
        with self.base.db() as db:
            row = db.execute('SELECT * FROM social_queries WHERE job_id=? AND done=0 AND retry_at<=? ORDER BY id LIMIT 1', (job_id,time.time())).fetchone()
            pending = db.execute('SELECT count(*) FROM social_queries WHERE job_id=? AND done=0', (job_id,)).fetchone()[0]
        return ({**dict(row),'data':json.loads(row['data'])} if row else None), pending

    def reserve_request(self, job_id, token):
        job = self.job(job_id)
        with self.base.db() as db:
            changed = db.execute('UPDATE social_jobs SET requests=requests+1 WHERE id=? AND owner=? AND requests<? AND stop_requested=0', (job_id,token,job['settings']['max_requests'])).rowcount
        self.checkpoint()
        return bool(changed)

    def defer_engine(self, job_id, engine, seconds):
        with self.base.db() as db:
            rows = db.execute('SELECT id,data FROM social_queries WHERE job_id=? AND done=0', (job_id,)).fetchall()
            db.executemany('UPDATE social_queries SET retry_at=? WHERE id=?',
                           [(time.time()+seconds,r['id']) for r in rows if json.loads(r['data'])['engine']==engine])
        self.checkpoint()

    def source_slot(self, engine):
        with self.base.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT next_request_at FROM source_pacing WHERE source=?', (engine,)).fetchone()
            delay = max(0, row[0]-time.time()) if row else 0
            if not delay:
                db.execute('INSERT OR REPLACE INTO source_pacing VALUES(?,?)', (engine,time.time()+8))
        return delay

    def revise_limits(self, job_id, target, requests):
        job = self.job(job_id)
        if job['status']=='running' and job['heartbeat']>time.time()-90:
            raise ValueError('Pause this research before changing limits.')
        values = validate_settings({**job['settings'], 'target':target, 'max_requests':requests})
        if target < self.count(job_id) or requests < job['requests']:
            raise ValueError('Limits cannot be below the records or requests already used.')
        with self.base.db() as db:
            db.execute('UPDATE social_jobs SET settings=? WHERE id=?', (json.dumps(values),job_id))
        self.checkpoint()
