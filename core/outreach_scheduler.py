"""Local service: periodic Sheet sync and one explicitly enabled daily outreach cycle."""
from datetime import datetime
import os
from pathlib import Path
import subprocess
import sys
import time
import threading
from .config import BASE_DIR, LOG_DIR, config
from .local_lock import LocalLock
from .outreach_store import OutreachStore, TZ


def tick(store=None, now=None):
    from . import pipeline
    store=store or OutreachStore()
    now=time.time() if now is None else now
    prefs=store.settings()
    store.set('scheduler_heartbeat',now)
    last=store.get('scheduler_sync_attempt',0)
    if prefs['auto_sync_enabled'] and now-last>=300:
        store.set('scheduler_sync_attempt',now)
        try:
            pipeline.sync_local_leads(store)
        except Exception as exc:
            store.set('last_sync_error',{'at':now,'error':type(exc).__name__})
            store.event('Automatic Sheet sync failed: '+type(exc).__name__+'. Use Sync & review to inspect the connection.')
    local=datetime.fromtimestamp(now,TZ)
    if not prefs['auto_send_enabled'] or (local.hour,local.minute)<(prefs['hour'],prefs['minute']):
        return
    day=local.date().isoformat()
    if store.get('scheduled_day')==day:
        return
    # No bursts of catch-up runs after restarts or lost SMTP responses.
    store.set('scheduled_day',day)
    try:
        pipeline.reconcile(store)
        pipeline.run_cycle('followup',store=store,scheduled=True)
        pipeline.run_cycle('initial',store=store,scheduled=True)
    except Exception as exc:
        store.event('Scheduled outreach stopped: '+type(exc).__name__+'. Check the last run and reconcile before trying manually.')
    finally:
        store.set('scheduler_heartbeat',time.time())


def launch():
    if config.CLOUD_MODE:
        thread=threading.Thread(target=run,daemon=True,name='cloud-scheduler')
        thread.start()
        return thread
    LOG_DIR.mkdir(parents=True,exist_ok=True)
    with (LOG_DIR/'outreach-scheduler.log').open('a',encoding='utf-8') as log:
        process=subprocess.Popen([sys.executable,str(BASE_DIR/'outreach_worker.py')],cwd=BASE_DIR,
            stdout=log,stderr=log,stdin=subprocess.DEVNULL,
            creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0) if os.name=='nt' else 0,
            start_new_session=os.name!='nt')
    return process


def run():
    store=OutreachStore()
    try:
        with LocalLock(Path(store.path).with_suffix('.scheduler.lock')):
            store.event('Local Sheet sync and outreach scheduler started.')
            while True:
                try:
                    tick(store)
                except Exception as exc:
                    print('Scheduler iteration failed: '+type(exc).__name__, flush=True)
                time.sleep(15)
    except ValueError as exc:
        # Another service instance owns the OS lock.
        print(str(exc))
