import asyncio
import json
from pathlib import Path
from urllib.parse import urlparse

import httpx

from src.browser import browser_context
from src.config import LOGS_DIR, RunSettings
from src.result import VerifyReportRow, write_verify_csv
from tools.verifier import verify_url


EXCLUDED_PATH = Path("data/excluded_domains.json")


def to_url(domain: str) -> str:
    if domain.startswith(("http://", "https://")):
        return domain.rstrip("/")
    return f"https://{domain}"


async def main() -> None:
    excluded = json.loads(EXCLUDED_PATH.read_text(encoding="utf-8"))
    urls = [to_url(domain) for domain in excluded.keys()]
    concurrency = 4
    settings = RunSettings(
        phone="+375 (44) 775-88-86",
        message="",
        headless=True,
        per_site_timeout_ms=25000,
    )

    rows: list[VerifyReportRow] = []

    async with browser_context(headless=settings.headless) as browser:
        async with httpx.AsyncClient(follow_redirects=True, verify=False) as client:
            semaphore = asyncio.Semaphore(concurrency)

            async def check(url: str) -> None:
                async with semaphore:
                    result = await verify_url(
                        url,
                        browser,
                        client,
                        test_submission=True,
                        submission_settings=settings,
                    )
                    rows.append(
                        VerifyReportRow(
                            url=result.url,
                            alive=result.alive,
                            form_found=result.form_found,
                            submission_checked=result.submission_checked,
                            phone_sent=result.phone_sent,
                            submit_ok=result.submit_ok,
                            good=result.good,
                            reason=result.reason,
                            timestamp=result.timestamp,
                        )
                    )
                    print(
                        f"{result.url}\tgood={result.good}\talive={result.alive}\t"
                        f"form={result.form_found}\tphone={result.phone_sent}\t"
                        f"submit={result.submit_ok}\treason={result.reason}"
                    )

            await asyncio.gather(*(check(url) for url in urls))

    rows.sort(key=lambda item: item.url)
    report_path = LOGS_DIR / "verify_excluded_recheck_20260508.csv"
    write_verify_csv(rows, report_path)

    good = sum(1 for row in rows if row.good)
    bad = len(rows) - good
    print(f"SUMMARY\ttotal={len(rows)}\tgood={good}\tbad={bad}\treport={report_path}")


if __name__ == "__main__":
    asyncio.run(main())