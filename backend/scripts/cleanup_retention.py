"""Explicit operator-triggered retention cleanup. Default API startup cleanup remains disabled."""
import argparse
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.main import initialize, purge_expired_data

parser = argparse.ArgumentParser()
parser.add_argument("--days", type=int, required=True, help="Delete documents older than this many days")
args = parser.parse_args()
if args.days < 1:
    parser.error("--days must be at least 1")
initialize()
print(f"Deleted {purge_expired_data(args.days)} expired document(s)")
