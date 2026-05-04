from __future__ import annotations

import asyncio
import logging
import random
from typing import Awaitable, Callable, Iterable

from playwright.async_api import TimeoutError as PWTimeoutError

from .browser import browser_context
from .config import RunSettings
from .form_filler import submit_form
from .form_finder import find_form
from .result import SiteResult, Status

log = logging.getLogger(__name__)

ProgressCb = Callable[[SiteResult], Awaitable[None] | None]


async def _process_site(context, url: str, settings: RunSettings) -> SiteResult:
    result = SiteResult(site=url)
    page = await context.new_page()
    page.set_default_timeout(settings.per_site_timeout_ms)
    try:
        try:
            await page.goto(
                url, wait_until="domcontentloaded", timeout=settings.per_site_timeout_ms
            )
        except PWTimeoutError:
            result.status = Status.TIMEOUT
            result.error = "таймаут загрузки страницы"
            return result
        except Exception as e:
            result.status = Status.UNREACHABLE
            result.error = f"{type(e).__name__}: {e}"
            return result

        try:
            await page.wait_for_load_state("networkidle", timeout=4000)
        except PWTimeoutError:
            pass

        handle = await find_form(page)
        if handle is None:
            result.status = Status.NO_FORM
            result.error = "callback-форма не обнаружена"
            return result

        result.form_found = True
        status, phone_sent, msg_sent, err = await submit_form(
            page, handle, settings.phone, settings.message
        )
        result.status = status
        result.phone_sent = phone_sent
        result.message_sent = msg_sent
        result.error = err
        return result
    except Exception as e:
        log.exception("unexpected failure on %s", url)
        result.status = Status.FAILED
        result.error = f"{type(e).__name__}: {e}"
        return result
    finally:
        try:
            await page.close()
        except Exception:
            pass


async def run(
    urls: Iterable[str],
    settings: RunSettings,
    on_progress: ProgressCb | None = None,
) -> list[SiteResult]:
    results: list[SiteResult] = []
    urls = list(urls)[: settings.max_sites]
    
    # Проверка лимита на отправки (защита от DDoS)
    if len(urls) > settings.max_daily_submissions:
        raise ValueError(
            f"Количество сайтов ({len(urls)}) превышает дневной лимит "
            f"({settings.max_daily_submissions}). "
            "Ограничение установлено в целях безопасности."
        )
    
    async with browser_context(headless=settings.headless) as ctx:
        for idx, url in enumerate(urls):
            res = await _process_site(ctx, url, settings)
            results.append(res)
            if on_progress is not None:
                maybe = on_progress(res)
                if asyncio.iscoroutine(maybe):
                    await maybe
            if idx < len(urls) - 1:
                pause = random.uniform(settings.pause_min, settings.pause_max)
                await asyncio.sleep(pause)
    return results
