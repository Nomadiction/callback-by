"""Общая логика верификации: жив ли домен, есть ли callback-форма,
и при необходимости проходит ли тест ввода номера и submit.

Используется и в verify_sites.py, и в grow_list.py.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import urlparse

import httpx
from playwright.async_api import BrowserContext, TimeoutError as PWTimeoutError

from src.config import RunSettings
from src.form_filler import submit_form
from src.form_finder import find_form, has_captcha
from src.result import Status

PROBE_TIMEOUT = 8.0
PAGE_TIMEOUT_MS = 15_000  # Оптимизирован таймаут
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ru-RU,ru;q=0.9",
}


@dataclass
class VerifyResult:
    url: str
    alive: bool = False
    form_found: bool = False
    phone_sent: bool = False
    submit_ok: bool = False
    submission_checked: bool = False
    reason: str = ""
    timestamp: str = ""

    @property
    def good(self) -> bool:
        if not (self.alive and self.form_found):
            return False
        if not self.submission_checked:
            return True
        return self.phone_sent and self.submit_ok


def _normalize_url(url: str) -> str:
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    return url.rstrip("/")


async def probe_alive(url: str, client: httpx.AsyncClient) -> tuple[bool, str]:
    """HTTP GET c коротким таймаутом.
    
    Принимаем 2xx/3xx и даже 403 (бот-протекция, но домен жив).
    """
    try:
        resp = await client.get(
            url, headers=HEADERS, timeout=PROBE_TIMEOUT, follow_redirects=True
        )
    except httpx.TimeoutException:
        return False, "timeout"
    except httpx.ConnectError as e:
        return False, f"connect: {e}"
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"
    if resp.status_code >= 500:
        return False, f"HTTP {resp.status_code}"
    return True, ""


async def probe_form(url: str, ctx: BrowserContext) -> tuple[bool, str]:
    page = await ctx.new_page()
    page.set_default_timeout(PAGE_TIMEOUT_MS)
    try:
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT_MS)
        except PWTimeoutError:
            return False, "page timeout"
        except Exception as e:
            return False, f"goto: {type(e).__name__}"
        try:
            await page.wait_for_load_state("networkidle", timeout=4000)
        except PWTimeoutError:
            pass
        try:
            if await has_captcha(page):
                return False, "captcha detected"
        except Exception:
            pass
        try:
            handle = await asyncio.wait_for(
                find_form(page, budget_sec=14.0), timeout=18.0
            )
        except asyncio.TimeoutError:
            return False, "form-finder timeout"
        except Exception as e:
            return False, f"finder error: {type(e).__name__}"
        if handle is None:
            return False, "no callback form"
        return True, ""
    finally:
        try:
            await page.close()
        except Exception:
            pass


async def verify_url(
    url: str,
    ctx: BrowserContext,
    client: httpx.AsyncClient,
    *,
    test_submission: bool = False,
    submission_settings: RunSettings | None = None,
) -> VerifyResult:
    norm = _normalize_url(url)
    res = VerifyResult(url=norm, timestamp=datetime.now().isoformat(timespec="seconds"))
    res.alive, alive_reason = await probe_alive(norm, client)
    if not res.alive:
        res.reason = alive_reason
        return res
    res.form_found, form_reason = await probe_form(norm, ctx)
    if not res.form_found:
        res.reason = form_reason
        return res
    if not test_submission:
        return res
    if submission_settings is None or not submission_settings.phone:
        res.reason = "submission test requires phone"
        return res
    res.submission_checked = True

    page = await ctx.new_page()
    page.set_default_timeout(submission_settings.per_site_timeout_ms)
    try:
        try:
            await page.goto(
                norm,
                wait_until="domcontentloaded",
                timeout=submission_settings.per_site_timeout_ms,
            )
        except PWTimeoutError:
            res.reason = "page timeout"
            return res
        except Exception as e:
            res.reason = f"goto: {type(e).__name__}"
            return res
        try:
            await page.wait_for_load_state("networkidle", timeout=4000)
        except PWTimeoutError:
            pass
        handle = await find_form(page)
        if handle is None:
            res.form_found = False
            res.reason = "no callback form"
            return res
        status, phone_sent, _msg_sent, err = await submit_form(
            page,
            handle,
            submission_settings.phone,
            submission_settings.message,
        )
        res.phone_sent = phone_sent
        res.submit_ok = status in (Status.SUCCESS, Status.SUBMITTED)
        if not res.phone_sent:
            res.reason = err or "phone input failed"
        elif not res.submit_ok:
            res.reason = err or status.value
        else:
            res.reason = status.value
    finally:
        try:
            await page.close()
        except Exception:
            pass
    return res


def domain_root(url: str) -> str | None:
    try:
        netloc = urlparse(_normalize_url(url)).netloc.lower()
    except Exception:
        return None
    if netloc.startswith("www."):
        netloc = netloc[4:]
    return netloc or None
