"""Local service: periodic Sheet sync and one explicitly enabled daily outreach cycle."""
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
import os
from pathlib import Path
import subprocess
import sys
import time
import threading
from .config import BASE_DIR, LOG_DIR, config
from .local_lock import LocalLock
from .outreach_store import OutreachStore, TZ


def next_run(prefs,now=None,last_day=''):
    now=time.time() if now is None else now
    if not (prefs['auto_send_enabled'] or prefs['auto_whatsapp_enabled']):
        return None
    zone=ZoneInfo(prefs['schedule_timezone'])
    local=datetime.fromtimestamp(now,zone)
    for offset in range(8):
        date=(local+timedelta(days=offset)).date()
        day=date.isoformat()
        if date.weekday() not in prefs['schedule_weekdays'] or last_day in (day,f'{zone.key}|{day}'):
            continue
        candidate=datetime(date.year,date.month,date.day,prefs['hour'],prefs['minute'],tzinfo=zone)
        # Move nonexistent spring-forward times to the next valid wall-clock minute.
        for _ in range(180):
            roundtrip=candidate.astimezone(timezone.utc).astimezone(zone)
            if roundtrip.replace(tzinfo=None)==candidate.replace(tzinfo=None): break
            candidate+=timedelta(minutes=1)
        return max(candidate,local,key=lambda d:d.timestamp())
    return None


def tick(store=None,now=None):
    store=store or OutreachStore()
    with LocalLock(store.path+'.schedule-cycle.lock'):
        return _tick(store,now)


def _tick(store=None, now=None):
    from . import pipeline
    store=store or OutreachStore()
    now=time.time() if now is None else now
    prefs=store.settings()
    store.set('scheduler_heartbeat',now)
    last=store.get('scheduler_sync_attempt',0)
    if prefs['auto_sync_enabled'] and now-last>=prefs['sync_interval_minutes']*60:
        store.set('scheduler_sync_attempt',now)
        try:
            pipeline.sync_local_leads(store)
        except Exception as exc:
            store.set('last_sync_error',{'at':now,'error':type(exc).__name__})
            store.event('Automatic Sheet sync failed: '+type(exc).__name__+'. Use Sync & review to inspect the connection.')
    local=datetime.fromtimestamp(now,ZoneInfo(prefs['schedule_timezone']))
    if (not (prefs['auto_send_enabled'] or prefs['auto_whatsapp_enabled']) or
        local.weekday() not in prefs['schedule_weekdays'] or (local.hour,local.minute)<(prefs['hour'],prefs['minute'])):
        return
    day=local.date().isoformat()
    key=prefs['schedule_timezone']+'|'+day
    if store.get('scheduled_day') in (day,key):
        return
    # No bursts of catch-up runs after restarts or lost SMTP responses.
    store.set('scheduled_day',key)
    store.set('schedule_last_started',now)
    try:
        if prefs['auto_send_enabled']:
            pipeline.reconcile(store)
            if prefs['followup_enabled']: pipeline.run_cycle('followup',store=store,scheduled=True)
            if prefs['initial_enabled']: pipeline.run_cycle('initial',store=store,scheduled=True)
        if store.settings()['auto_whatsapp_enabled']:
            from .messaging import run_queue
            run_queue(store,scheduled=True)
        store.set('schedule_last_error','')
    except Exception as exc:
        store.set('schedule_last_error',type(exc).__name__)
        store.event('Scheduled outreach stopped: '+type(exc).__name__+'. Check the last run and reconcile before trying manually.')
    finally:
        store.set('scheduler_heartbeat',time.time())
        store.set('schedule_last_finished',time.time())


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
