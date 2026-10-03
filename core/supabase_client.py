"""Server-side Supabase connection and deployment checks."""
from .config import config


def get_client():
    if not config.SUPABASE_URL or not config.SUPABASE_SERVICE_ROLE_KEY:
        raise RuntimeError("SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY are required.")
    from supabase import create_client
    return create_client(config.SUPABASE_URL, config.SUPABASE_SERVICE_ROLE_KEY)


def check_schema():
    client = get_client()
    client.table("job_leases").select("name").limit(1).execute()
    client.table("cloud_state").select("name").limit(1).execute()
    # These probes cannot match a real owner and therefore make no state changes.
    probe={'lease_name':'__schema_probe__','lease_owner':'__no_owner__'}
    client.rpc('renew_workspace_lease',dict(probe,ttl_seconds=30)).execute()
    client.rpc('save_workspace_snapshot',dict(probe,state_name='__probe__',payload='',byte_count=0)).execute()
    return {"ok": True}
