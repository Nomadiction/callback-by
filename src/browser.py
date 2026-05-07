from __future__ import annotations

import random
from contextlib import asynccontextmanager
from playwright.async_api import async_playwright, Browser, BrowserContext

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
]

VIEWPORTS = [
    {"width": 1366, "height": 768},
    {"width": 1440, "height": 900},
    {"width": 1536, "height": 864},
    {"width": 1920, "height": 1080},
]

_STEALTH_INIT_JS = """
// hide webdriver flag
Object.defineProperty(Navigator.prototype, 'webdriver', { get: () => undefined });
// chrome.runtime
window.chrome = window.chrome || { runtime: {}, loadTimes: function(){}, csi: function(){} };
// plugins (must be non-empty)
Object.defineProperty(navigator, 'plugins', {
    get: () => [
        { name: 'PDF Viewer', filename: 'internal-pdf-viewer' },
        { name: 'Chrome PDF Viewer', filename: 'internal-pdf-viewer' },
        { name: 'Chromium PDF Viewer', filename: 'internal-pdf-viewer' },
    ],
});
// languages
Object.defineProperty(navigator, 'languages', { get: () => ['ru-RU', 'ru', 'en-US', 'en'] });
// permissions API
const orig = navigator.permissions && navigator.permissions.query;
if (orig) {
    navigator.permissions.query = (p) => (
        p && p.name === 'notifications'
            ? Promise.resolve({ state: Notification.permission })
            : orig.call(navigator.permissions, p)
    );
}
// WebGL vendor / renderer spoof
const getParameter = WebGLRenderingContext.prototype.getParameter;
WebGLRenderingContext.prototype.getParameter = function(p) {
    if (p === 37445) return 'Intel Inc.';
    if (p === 37446) return 'Intel Iris OpenGL Engine';
    return getParameter.call(this, p);
};
// hardwareConcurrency
Object.defineProperty(navigator, 'hardwareConcurrency', { get: () => 8 });
// deviceMemory
Object.defineProperty(navigator, 'deviceMemory', { get: () => 8 });
"""


@asynccontextmanager
async def browser_context(headless: bool = True):
    ua = random.choice(USER_AGENTS)
    viewport = random.choice(VIEWPORTS)
    async with async_playwright() as p:
        browser: Browser = await p.chromium.launch(
            headless=headless,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--disable-features=IsolateOrigins,site-per-process",
                "--disable-site-isolation-trials",
                "--no-sandbox",
                "--disable-dev-shm-usage",
            ],
        )
        context: BrowserContext = await browser.new_context(
            user_agent=ua,
            locale="ru-RU",
            timezone_id="Europe/Minsk",
            viewport=viewport,
            extra_http_headers={
                "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
                "Sec-Ch-Ua": (
                    '"Chromium";v="126", "Google Chrome";v="126", '
                    '"Not-A.Brand";v="99"'
                ),
                "Sec-Ch-Ua-Mobile": "?0",
                "Sec-Ch-Ua-Platform": '"Windows"',
                "Upgrade-Insecure-Requests": "1",
            },
        )
        await context.add_init_script(_STEALTH_INIT_JS)
        try:
            yield context
        finally:
            await context.close()
            await browser.close()
