"""Bounded Actions runner sharing the hosted workspace's exclusive lease."""
import os
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
os.environ['APP_ENV']='production'


def main(kind):
    from core.cloud_runtime import start_cloud_runtime
    from core.outreach_store import OutreachStore
    from core.store import Store
    if kind not in ('discovery','outreach'):
        raise SystemExit('Choose discovery or outreach.')
    if os.getenv('ENABLE_CLOUD_JOBS','').lower() != 'true':
        print('Cloud jobs are disabled. Enable explicitly after deployment checks.')
        return
    try:
        runtime=start_cloud_runtime()
    except ValueError as exc:
        print(str(exc))
        return
    try:
        store=Store()
        outreach=OutreachStore()
        if kind == 'discovery':
            from core.campaigns import run_job
            from core.pipeline import sync_local_leads
            job_id=os.getenv('DISCOVERY_JOB_ID','').strip()
            if not job_id:
                raise ValueError('DISCOVERY_JOB_ID must identify a saved campaign.')
            if store.job(job_id)['status'] != 'complete':
                run_job(job_id,store=store)
            print(sync_local_leads(outreach))
        else:
            from core.outreach_scheduler import tick
            tick(outreach)
    finally:
        runtime.close()


if __name__ == '__main__':
    if len(sys.argv)!=2:
        raise SystemExit('Usage: python scripts/cloud_cycle.py outreach|discovery')
    main(sys.argv[1])
