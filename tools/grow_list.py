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
        pass_idx = payload.get("pass_idx")
        pass_total = payload.get("pass_total")
        prefix = f"pass {pass_idx}/{pass_total} | " if pass_idx and pass_total else ""
        payload_text = f"\n>>> {prefix}[{payload['current']}/{payload['target']}] "
        payload_text += f"query: {payload['query']}"
        print(payload_text, flush=True)
    elif event == "query_done":
        raw = payload.get("raw_total", "?")
        known = payload.get("already_known", 0)
        excl = payload.get("already_excluded", 0)
        src = payload.get("source", "?")
        if payload["candidates"] == 0:
            reason = payload.get("reason", "")
            print(
                f"  (raw={raw} src={src} known={known} excluded={excl}) "
                f"— {reason}, empty_rounds={payload['empty_rounds']}",
                flush=True,
            )
        else:
            print(
                f"  → raw={raw} src={src} known={known} excluded={excl} "
                f"→ fresh={payload['candidates']}, verifying...",
                flush=True,
            )
    elif event == "candidate_good":
        print(
            f"  ✓ {payload['domain']}  [{payload['current']}/{payload['target']}]",
            flush=True,
        )
    elif event == "candidate_bad":
        print(f"  ✗ {payload['domain']} — {payload['reason']}", flush=True)
    elif event == "browser_recycled":
        print(
            f"  ↻ браузер перезапущен после {payload.get('after')} кандидатов",
            flush=True,
        )
    elif event == "finished":
        print(
            f"\ndone: +{payload['new_good']} good, "
            f"skipped {payload['skipped']} junk, total in file: {payload['total']} "
            f"(stop: {payload.get('stop_reason', 'unknown')})",
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
    p.add_argument(
        "--max-empty-rounds",
        type=int,
        default=15,
        help=(
            "stop after N consecutive queries without fresh candidates "
            "(default: 15)"
        ),
    )
    p.add_argument(
        "--browser-recycle-every",
        type=int,
        default=60,
        help="restart Chromium after N candidates (default: 60)",
    )
    p.add_argument(
        "--max-query-passes",
        type=int,
        default=3,
        help="repeat the full query list up to N times while new sites still appear (default: 3)",
    )
    args = p.parse_args()
    asyncio.run(grow(
        target=args.target,
        headless=not args.headed,
        concurrency=args.concurrency,
        on_progress=_print_event,
        dry_run=args.dry_run,
        max_empty_rounds=args.max_empty_rounds,
        browser_recycle_every=args.browser_recycle_every,
        max_query_passes=args.max_query_passes,
    ))


if __name__ == "__main__":
    sys.exit(main())
