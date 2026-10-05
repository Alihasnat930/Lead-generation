"""Local social research process, launched by the existing dashboard."""
import argparse
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from core.social_store import SocialStore
from core.social_worker import run

if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--job',required=True)
    parser.add_argument('--db',required=True)
    parser.add_argument('--token',required=True)
    args=parser.parse_args()
    run(args.job,SocialStore(args.db),args.token)
