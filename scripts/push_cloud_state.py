"""Upload local SQLite ledgers to Supabase cloud_state for scheduler bootstrap."""
import argparse
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

from core.config import config
from core.supabase_client import check_schema
from core.supabase_state import SupabaseState


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--campaign-db", default=config.DATABASE_PATH)
    parser.add_argument("--outreach-db", default=config.OUTREACH_DATABASE_PATH)
    parser.add_argument("--campaign-key", default="campaign_store.db")
    parser.add_argument("--outreach-key", default="outreach_store.db")
    args = parser.parse_args()

    check_schema()
    state = SupabaseState()

    campaign_path = Path(args.campaign_db)
    outreach_path = Path(args.outreach_db)
    if not campaign_path.exists() and not outreach_path.exists():
        raise SystemExit("No local SQLite ledgers found. Run the app once or pass explicit paths.")

    owner=state.acquire_lease('prospect-workspace',120)
    if not owner:
        raise SystemExit('Workspace is active in the cloud. Stop it before bootstrapping local data.')
    try:
        for key,path in ((args.campaign_key,campaign_path),(args.outreach_key,outreach_path)):
            if path.exists():
                existing=state.client.table('cloud_state').select('name').eq('name',key).limit(1).execute()
                if existing.data:
                    raise SystemExit('Bootstrap refused: cloud snapshots already exist. Existing cloud history must not be overwritten.')
        for key,path in ((args.campaign_key,campaign_path),(args.outreach_key,outreach_path)):
            if path.exists():
                state.renew_lease('prospect-workspace',owner,120)
                state.upload_file(key,str(path),lease_name='prospect-workspace',owner=owner)
                print(f'Bootstrapped {key}.')
        if outreach_path.exists():
            from core.outreach_store import OutreachStore
            OutreachStore(outreach_path).set('cloud_migrated',True)
            print('Local sending disabled for the migrated workspace. Use the hosted app.')
    finally:
        state.release_lease('prospect-workspace',owner)


if __name__ == "__main__":
    main()
