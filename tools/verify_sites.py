"""CLI: проверяет sites.json (HTTP + есть ли callback-форма).

Сайты без формы или мёртвые удаляются из sites.json и попадают в исключения
(data/excluded_domains.json), чтобы не перепроверяться при росте.

Использование:
    python -m tools.verify_sites                 # обычный прогон, удаляет нерабочие,
                                                    # копит исключения
    python -m tools.verify_sites --dry-run       # отчёт без записи
    python -m tools.verify_sites --headed        # с окном браузера (отладка)
    python -m tools.verify_sites --concurrency 4
    python -m tools.verify_sites --rerun-excluded  # сбросить исключения перед прогоном
"""
from __future__ import annotations

import argparse
import asyncio
import sys

from src.config import SITES_FILE, load_sites
from src.exclusions import clear as clear_exclusions
from src.maintenance import verify_all
from tools.verifier import VerifyResult


def _print_progress(i: int, total: int, entry: dict, res: VerifyResult):
    mark = "✓" if res.good else ("⚠" if res.alive else "✗")
    print(f"  [{i}/{total}] {mark} {entry['url']:<50} {res.reason}", flush=True)


async def _amain(args):
    if args.rerun_excluded:
        n = clear_exclusions()
        print(f"cleared {n} excluded domains\n", flush=True)

    sites = load_sites()
    print(
        f"verifying {len(sites)} sites (concurrency={args.concurrency})...\n",
        flush=True,
    )
    new_list, pairs = await verify_all(
        sites=sites,
        headless=not args.headed,
        concurrency=args.concurrency,
        on_progress=_print_progress,
        save=not args.dry_run,
    )
    good = sum(1 for _, r in pairs if r.good)
    alive_no_form = sum(1 for _, r in pairs if r.alive and not r.form_found)
    dead = sum(1 for _, r in pairs if not r.alive)
    removed = len(sites) - len(new_list)
    print(
        f"\nsummary: total={len(sites)} ✓good={good} "
        f"⚠no-form={alive_no_form} ✗dead={dead} "
        f"→ removed {removed} (added to exclusions)",
    )
    if args.dry_run:
        print("(dry-run, no changes saved)")
    else:
        print(f"saved {SITES_FILE}")


def main():
    p = argparse.ArgumentParser(
        description="Verify sites.json: alive + has callback form",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="only print report, don't modify sites.json",
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
        "--rerun-excluded",
        action="store_true",
        help=(
            "clear data/excluded_domains.json before verifying "
            "(force re-check of previously rejected domains)"
        ),
    )
    args = p.parse_args()
    asyncio.run(_amain(args))


if __name__ == "__main__":
    sys.exit(main())
