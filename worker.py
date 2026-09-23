"""Run a campaign independently of the browser/Streamlit session."""
import argparse
from core.campaigns import run_job
from core.store import Store

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--job", required=True)
    parser.add_argument("--token")
    parser.add_argument("--db")
    args = parser.parse_args()
    try:
        run_job(args.job, Store(args.db), args.token)
    except Exception as exc:
        # Avoid tracebacks that may contain third-party request credentials.
        print(f"Campaign worker stopped: {type(exc).__name__}", flush=True)
        raise SystemExit(1)
