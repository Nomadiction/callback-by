"""CLI: растит sites.json через DDG-discovery.

Использование:
    python -m tools.grow_list --target 300
    python -m tools.grow_list --target 300 --headed
    python -m tools.grow_list --target 300 --concurrency 4
"""
from __future__ import annotations

import argparse
import asyncio
import sys

from src.maintenance import grow


def _print_event(event: str, payload: dict):
    if event == "query_start":
        payload_text = f"\n>>> [{payload['current']}/{payload['target']}] "
        payload_text += f"query: {payload['query']}"
        print(payload_text, flush=True)
    elif event == "query_done":
        if payload["candidates"] == 0:
            print(
                f"  (no new candidates, empty_rounds={payload['empty_rounds']})",
                flush=True,
            )
        else:
            print(
                f"  → {payload['candidates']} new candidates, verifying...",
                flush=True,
            )
    elif event == "candidate_good":
        print(
            f"  ✓ {payload['domain']}  [{payload['current']}/{payload['target']}]",
            flush=True,
        )
    elif event == "candidate_bad":
        print(f"  ✗ {payload['domain']} — {payload['reason']}", flush=True)
    elif event == "finished":
        print(
            f"\ndone: +{payload['new_good']} good, "
            f"skipped {payload['skipped']} junk, total in file: {payload['total']}",
            flush=True,
        )


def main():
    p = argparse.ArgumentParser(
        description="Grow sites.json via DDG discovery + verification",
    )
    p.add_argument(
        "--target",
        type=int,
        default=300,
        help="target count of enabled sites",
    )
    p.add_argument(
        "--headed",
        action="store_true",
        help="show browser window",
    )
    p.add_argument(
        "--concurrency",
        type=int,
        default=3,
        help="parallel verifications",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="dry run: search only, don't save changes",
    )
    args = p.parse_args()
    asyncio.run(grow(
        target=args.target,
        headless=not args.headed,
        concurrency=args.concurrency,
        on_progress=_print_event,
        dry_run=args.dry_run,
    ))


if __name__ == "__main__":
    sys.exit(main())
