import asyncio
from pathlib import Path

import httpx

from src.browser import browser_context
from src.config import RunSettings
from tools.verifier import verify_url


URLS_PATH = Path("data/recheck_old_failures.txt")


async def main() -> None:
    urls = [line.strip() for line in URLS_PATH.read_text(encoding="utf-8").splitlines() if line.strip()]
    concurrency = 4
    settings = RunSettings(
        headless=True,
        per_site_timeout_ms=25000,
        phone="+375 (44) 775-88-86",
        message="",
    )

    async with browser_context(headless=settings.headless) as browser:
        async with httpx.AsyncClient(follow_redirects=True, verify=False) as client:
            semaphore = asyncio.Semaphore(concurrency)
            results = []

            async def check(url: str) -> None:
                async with semaphore:
                    result = await verify_url(
                        url,
                        browser,
                        client,
                        test_submission=True,
                        submission_settings=settings,
                    )
                    results.append(result)
                    print(
                        f"{url}\tgood={result.good}\talive={result.alive}\t"
                        f"form={result.form_found}\tphone={result.phone_sent}\t"
                        f"submit={result.submit_ok}\treason={result.reason}"
                    )

            await asyncio.gather(*(check(url) for url in urls))

    good = sum(1 for item in results if item.good)
    bad = len(results) - good
    print(f"SUMMARY\ttotal={len(results)}\tgood={good}\tbad={bad}")


if __name__ == "__main__":
    asyncio.run(main())