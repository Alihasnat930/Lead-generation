"""Resumable social research; no messaging or CRM writes."""
import subprocess
import sys
import threading
import time
from .config import BASE_DIR, LOG_DIR, config
from .local_lock import LocalLock
from .providers import ProviderError
from .social_sources import search


def launch(job_id, store):
    token = store.reserve(job_id)
    try:
        if config.CLOUD_MODE:
            threading.Thread(target=run, args=(job_id,store,token),daemon=True,name='social-research').start()
        else:
            LOG_DIR.mkdir(parents=True,exist_ok=True)
            with (LOG_DIR/'social-research.log').open('a',encoding='utf-8') as log:
                process = subprocess.Popen([sys.executable,str(BASE_DIR/'scripts'/'social_worker.py'),'--job',job_id,'--db',store.path,'--token',token],
                    cwd=BASE_DIR,stdout=log,stderr=log,stdin=subprocess.DEVNULL,
                    creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0) if sys.platform=='win32' else 0)
            threading.Thread(target=process.wait,daemon=True).start()
    except Exception:
        store.finish(job_id,token,'failed','Could not start the research worker.')
        raise


def run(job_id, store, token, fetch=search, sleeper=time.sleep, max_seconds=3600):
    try:
        with LocalLock(store.path+'.social-worker.lock'):
            started = time.monotonic()
            while store.pulse(job_id,token):
                job = store.job(job_id)
                if job['stop_requested'] or time.monotonic()-started>=max_seconds:
                    store.finish(job_id,token,'paused','Progress saved. Resume when ready.')
                    return
                if store.count(job_id)>=job['settings']['target']:
                    store.finish(job_id,token,'target_reached','Research-record target reached. Review the source posts before treating them as opportunities.')
                    return
                query, pending = store.next_query(job_id)
                if not query:
                    store.finish(job_id,token,'blocked' if pending else 'exhausted',
                                 'Sources are cooling down; retry later. '+job['reason'][:350] if pending else 'Saved searches reached their page limits or returned no more results. Records are saved; broaden the next run or import an export.')
                    return
                if job['requests']>=job['settings']['max_requests']:
                    store.finish(job_id,token,'request_limit','Request allowance reached. Increase it and resume to continue.')
                    return
                wait = store.source_slot(query['data']['engine'])
                if wait:
                    sleeper(min(wait,2))
                    continue
                if not store.reserve_request(job_id,token):
                    continue
                try:
                    result = fetch(query['data'],query['page'],job['settings'])
                except ProviderError as exc:
                    store.defer_engine(job_id,query['data']['engine'],getattr(exc,'retry_after',300))
                    with store.base.db() as db:
                        db.execute('UPDATE social_jobs SET reason=? WHERE id=? AND owner=?',(str(exc),job_id,token))
                    continue
                store.save_records(job_id,result.items,token,query,result.has_more,getattr(result,'cursor',''))
    except Exception as exc:
        try:
            store.finish(job_id,token,'failed','Research stopped ('+type(exc).__name__+'). Saved records are retained; check storage before resuming.')
        except Exception:
            pass
