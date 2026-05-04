from __future__ import annotations

from contextlib import asynccontextmanager
from playwright.async_api import async_playwright, Browser, BrowserContext

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


@asynccontextmanager
async def browser_context(headless: bool = True):
    async with async_playwright() as p:
        browser: Browser = await p.chromium.launch(
            headless=headless,
            args=["--disable-blink-features=AutomationControlled"],
        )
        context: BrowserContext = await browser.new_context(
            user_agent=USER_AGENT,
            locale="ru-RU",
            viewport={"width": 1366, "height": 900},
        )
        try:
            yield context
        finally:
            await context.close()
            await browser.close()
