"""Read the call log from the command line.

  python -m fake_payer.calls list [--limit N]     recent calls, newest first
  python -m fake_payer.calls show REFERENCE        one call, with its transcript, as JSON
  python -m fake_payer.calls cleanup               remove entries older than the retention period

Reads CALL_LOG_DIR and LOG_RETENTION_DAYS from the environment (and a .env in the working directory), like the line itself.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import List, Optional

from .__main__ import _load_dotenv
from .calllog import CallLog


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m fake_payer.calls", description=__doc__.split("\n")[0])
    commands = parser.add_subparsers(dest="command", required=True)
    listing = commands.add_parser("list", help="recent calls, newest first")
    listing.add_argument("--limit", type=int, default=20)
    show = commands.add_parser("show", help="one call, by its reference number")
    show.add_argument("reference")
    commands.add_parser("cleanup", help="remove entries older than the retention period")
    args = parser.parse_args(argv)

    _load_dotenv(Path(".env"))
    log = CallLog(os.environ.get("CALL_LOG_DIR") or "calls")
    if args.command == "list":
        for entry in log.recent(args.limit):
            print(f"{entry['time']}  {entry['reference']}  {entry['caller']:<9} {entry['ended']:<16} {entry['duration_seconds']:>6}s  turns={entry['turns']}  ungrounded={entry['ungrounded']}  reply latency median/max={entry['latency_median_ms']}/{entry['latency_max_ms']} ms")
        return 0
    if args.command == "show":
        record = log.get(args.reference)
        if record is None:
            print(f"no call with reference {args.reference}", file=sys.stderr)
            return 1
        print(json.dumps(record, indent=2))
        return 0
    days = float(os.environ.get("LOG_RETENTION_DAYS") or 7)
    print(f"removed {log.cleanup(days)} call(s) older than {days:g} days")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
