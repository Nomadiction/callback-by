"""CLI: проверяет sites.json.

Сайты без формы или мёртвые удаляются из sites.json и попадают в исключения
(data/excluded_domains.json), чтобы не перепроверяться при росте.

В строгом режиме дополнительно проверяются вставка тестового номера и submit.

Использование:
    python -m tools.verify_sites                 # обычный прогон, удаляет нерабочие,
                                                    # копит исключения
    python -m tools.verify_sites --dry-run       # отчёт без записи
    python -m tools.verify_sites --headed        # с окном браузера (отладка)
    python -m tools.verify_sites --concurrency 4
    python -m tools.verify_sites --strict --phone "+375291234567"
    python -m tools.verify_sites --rerun-excluded  # сбросить исключения перед прогоном
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime

from src.config import LOGS_DIR, RunSettings, SITES_FILE, load_sites
from src.exclusions import clear as clear_exclusions
from src.maintenance import verify_all
from src.phone import normalize
from src.result import VerifyReportRow, write_verify_csv
from tools.verifier import VerifyResult


def _print_progress(i: int, total: int, entry: dict, res: VerifyResult):
    mark = "✓" if res.good else ("⚠" if res.alive else "✗")
    extra = ""
    if res.submission_checked:
        extra = (
            f" phone={'ok' if res.phone_sent else 'fail'}"
            f" submit={'ok' if res.submit_ok else 'fail'}"
        )
    print(f"  [{i}/{total}] {mark} {entry['url']:<50} {res.reason}{extra}", flush=True)


async def _amain(args):
    if args.rerun_excluded:
        n = clear_exclusions()
        print(f"cleared {n} excluded domains\n", flush=True)

    verify_settings = None
    if args.strict:
        if not args.phone:
            raise SystemExit("--strict requires --phone")
        verify_settings = RunSettings(
            phone=normalize(args.phone),
            message=args.message,
            headless=not args.headed,
            pause_min=0,
            pause_max=0,
            max_sites=1,
        )

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
        test_submission=args.strict,
        submission_settings=verify_settings,
    )
    good = sum(1 for _, r in pairs if r.good)
    alive_no_form = sum(1 for _, r in pairs if r.alive and not r.form_found)
    dead = sum(1 for _, r in pairs if not r.alive)
    phone_failed = sum(
        1 for _, r in pairs if r.submission_checked and r.alive and r.form_found and not r.phone_sent
    )
    submit_failed = sum(
        1
        for _, r in pairs
        if r.submission_checked and r.alive and r.form_found and r.phone_sent and not r.submit_ok
    )
    removed = len(sites) - len(new_list)
    report_rows = [
        VerifyReportRow(
            url=entry["url"],
            alive=res.alive,
            form_found=res.form_found,
            submission_checked=res.submission_checked,
            phone_sent=res.phone_sent,
            submit_ok=res.submit_ok,
            good=res.good,
            reason=res.reason,
        )
        for entry, res in pairs
    ]
    report_path = LOGS_DIR / (
        f"verify_results_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    )
    write_verify_csv(report_rows, report_path)
    print(
        f"\nsummary: total={len(sites)} ✓good={good} "
        f"⚠no-form={alive_no_form} ✗dead={dead} "
        + (f" ⛔phone-failed={phone_failed}" if args.strict else "")
        + (f" ⛔submit-failed={submit_failed}" if args.strict else "")
        + f" → removed {removed} (added to exclusions)",
    )
    print(f"verify report: {report_path}")
    if args.dry_run:
        print("(dry-run, no changes saved)")
    else:
        print(f"saved {SITES_FILE}")


def main():
    p = argparse.ArgumentParser(
        description="Verify sites.json: alive + callback form, optionally strict submit test",
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
    p.add_argument(
        "--strict",
        action="store_true",
        help="also test phone input and submit using a test number",
    )
    p.add_argument(
        "--phone",
        help="test phone number for --strict mode",
    )
    p.add_argument(
        "--message",
        default="Проверка формы",
        help="test message for --strict mode",
    )
    args = p.parse_args()
    asyncio.run(_amain(args))


if __name__ == "__main__":
    sys.exit(main())
