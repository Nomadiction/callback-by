"""Поиск .by-кандидатов через стелс-Playwright + curl_cffi с TLS impersonation.

Главные источники blocking-обхода:
1. tf-playwright-stealth: патч navigator.webdriver, plugins, languages, WebGL.
2. curl_cffi (impersonate=chrome): TLS/JA3-отпечаток реального Chrome.
3. Случайные UA, человекоподобные паузы, скролл, Referer.

Стратегия: пробуем движки в порядке Bing → DDG → Yandex, на каждом
сначала curl_cffi, потом стелс-Playwright. Возвращаем первый непустой.
"""
from __future__ import annotations

import asyncio
import logging
import random
import re
from typing import Iterable
from urllib.parse import parse_qs, quote_plus, unquote, urlparse

from playwright.async_api import (
    BrowserContext,
    Page,
    TimeoutError as PWTimeoutError,
)

try:
    from playwright_stealth import Stealth
    _STEALTH = Stealth()
except Exception:  # pragma: no cover
    _STEALTH = None

try:
    from curl_cffi import requests as cffi_requests
    _CFFI_OK = True
except Exception:  # pragma: no cover
    cffi_requests = None
    _CFFI_OK = False

log = logging.getLogger(__name__)

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:126.0) Gecko/20100101 "
    "Firefox/126.0",
]

HEADERS = {
    "User-Agent": USER_AGENTS[0],
    "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/webp,*/*;q=0.8"
    ),
}

EXCLUDE_DOMAINS = {
    "vk.com", "facebook.com", "instagram.com", "youtube.com",
    "google.com", "duckduckgo.com", "bing.com", "yandex.by", "yandex.ru",
    "wikipedia.org", "tut.by", "onliner.by",
    "twitter.com", "x.com", "t.me", "telegram.org",
    "ok.ru", "linkedin.com", "tiktok.com", "pinterest.com",
    "av.by", "kufar.by",
    "translate.google.com", "webcache.googleusercontent.com",
    "go.microsoft.com", "support.microsoft.com",
}

PAGE_GOTO_TIMEOUT_MS = 25_000
NETWORK_IDLE_MS = 5_000


_URL_ENCODED_JUNK_RE = re.compile(r"^(2[fdcb]|3[ab]|252[fdcb])", re.I)


def _domain_root(url: str) -> str | None:
    try:
        netloc = urlparse(url).netloc.lower()
    except Exception:
        return None
    netloc = re.sub(r"^www\.", "", netloc)
    netloc = netloc.split(":", 1)[0]
    if not netloc.endswith(".by"):
        return None
    if netloc in EXCLUDE_DOMAINS:
        return None
    prefix = netloc[:-3]
    if not prefix or prefix.endswith("."):
        return None
    if _URL_ENCODED_JUNK_RE.match(prefix.split(".")[0]):
        return None
    if not re.match(r"^[a-z]", prefix):
        return None
    return netloc


def _dedup(items: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for x in items:
        if x and x not in seen:
            seen.add(x)
            out.append(x)
    return out


def _extract_real_url(href: str) -> str | None:
    if not href:
        return None
    if href.startswith("//"):
        href = "https:" + href
    try:
        parsed = urlparse(href)
    except Exception:
        return None
    if "uddg" in (parsed.query or ""):
        qs = parse_qs(parsed.query)
        if "uddg" in qs:
            return unquote(qs["uddg"][0])
    if href.startswith(("http://", "https://")):
        return href
    return None


def _domains_from_text(text: str) -> list[str]:
    out: list[str] = []
    decoded = text
    try:
        decoded = unquote(unquote(text))
    except Exception:
        pass
    for m in re.finditer(r"https?://[^\s\"<>']+", decoded):
        d = _domain_root(m.group(0))
        if d:
            out.append(d)
    for m in re.finditer(r"\b([a-z][a-z0-9-]*(?:\.[a-z0-9-]+)*\.by)\b", decoded, re.I):
        cand = m.group(1).lower()
        d = _domain_root("https://" + cand)
        if d:
            out.append(d)
    return _dedup(out)


async def _human_pause(short: bool = False):
    base = random.uniform(0.4, 1.2) if short else random.uniform(2.0, 5.0)
    await asyncio.sleep(base)


async def _human_scroll(page: Page) -> None:
    try:
        for _ in range(random.randint(2, 4)):
            await page.mouse.wheel(0, random.randint(300, 700))
            await asyncio.sleep(random.uniform(0.3, 0.9))
    except Exception:
        pass


async def _apply_stealth(page: Page) -> None:
    if _STEALTH is None:
        return
    try:
        await _STEALTH.apply_stealth_async(page)
    except Exception as e:
        log.debug("stealth apply failed: %s", e)


async def _new_search_page(ctx: BrowserContext) -> Page:
    page = await ctx.new_page()
    page.set_default_timeout(PAGE_GOTO_TIMEOUT_MS)
    await _apply_stealth(page)
    return page


async def _hrefs_from_page(page: Page) -> list[str]:
    try:
        return await page.eval_on_selector_all(
            "a[href]", "els => els.map(e => e.href)"
        )
    except Exception:
        return []


def _domains_from_hrefs(hrefs: Iterable[str]) -> list[str]:
    out: list[str] = []
    for h in hrefs:
        real = _extract_real_url(h)
        if not real:
            continue
        d = _domain_root(real)
        if d:
            out.append(d)
    return out


# ──────────────────────────────────────────────────────────────────────
# curl_cffi paths (TLS impersonation)


_CFFI_IMPERSONATES = ["chrome120", "chrome116", "chrome110", "edge99"]


async def _cffi_get(url: str, *, referer: str | None = None) -> str | None:
    if not _CFFI_OK:
        return None

    def _do() -> str | None:
        impersonate = random.choice(_CFFI_IMPERSONATES)
        try:
            r = cffi_requests.get(
                url,
                headers={
                    "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
                    **({"Referer": referer} if referer else {}),
                },
                impersonate=impersonate,
                timeout=20,
            )
            if r.status_code != 200:
                log.info("cffi %s: HTTP %s", url[:60], r.status_code)
                return None
            return r.text
        except Exception as e:
            log.info("cffi %s: %s", url[:60], e)
            return None

    return await asyncio.to_thread(_do)


async def _cffi_post(url: str, *, data: dict) -> str | None:
    if not _CFFI_OK:
        return None

    def _do() -> str | None:
        impersonate = random.choice(_CFFI_IMPERSONATES)
        try:
            r = cffi_requests.post(
                url,
                data=data,
                headers={
                    "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
                    "Origin": "https://html.duckduckgo.com",
                    "Referer": "https://html.duckduckgo.com/",
                    "Content-Type": "application/x-www-form-urlencoded",
                },
                impersonate=impersonate,
                timeout=20,
            )
            if r.status_code != 200:
                log.info("cffi POST %s: HTTP %s", url[:60], r.status_code)
                return None
            return r.text
        except Exception as e:
            log.info("cffi POST %s: %s", url[:60], e)
            return None

    return await asyncio.to_thread(_do)


# ──────────────────────────────────────────────────────────────────────
# Bing


async def _bing_via_cffi(query: str, *, pages: int = 3) -> list[str]:
    out: list[str] = []
    for p in range(pages):
        first = p * 10 + 1
        url = (
            f"https://www.bing.com/search?q={quote_plus(query)}"
            f"&setlang=ru&count=20&first={first}"
        )
        html = await _cffi_get(url, referer="https://www.bing.com/")
        if not html:
            break
        if "captcha" in html.lower() and "verify" in html.lower():
            log.info("bing cffi captcha for %r", query)
            break
        page_doms = _domains_from_text(html)
        if not page_doms:
            break
        out.extend(page_doms)
        await asyncio.sleep(random.uniform(1.5, 3.0))
    return _dedup(out)


async def _bing_via_playwright(
    query: str, ctx: BrowserContext, *, pages: int = 3
) -> list[str]:
    page = await _new_search_page(ctx)
    out: list[str] = []
    try:
        try:
            await page.goto(
                "https://www.bing.com/",
                wait_until="domcontentloaded",
                timeout=PAGE_GOTO_TIMEOUT_MS,
            )
            await _human_pause(short=True)
            try:
                accept = page.locator(
                    'button:has-text("Accept"), '
                    'button:has-text("Принять"), '
                    'button#bnp_btn_accept'
                )
                if await accept.count() > 0 and await accept.first.is_visible():
                    await accept.first.click(timeout=2000)
                    await _human_pause(short=True)
            except Exception:
                pass
        except Exception as e:
            log.info("bing home goto failed: %s", e)

        for p in range(pages):
            first = p * 10 + 1
            url = (
                f"https://www.bing.com/search?q={quote_plus(query)}"
                f"&setlang=ru&count=20&first={first}"
            )
            try:
                await page.goto(
                    url, wait_until="domcontentloaded", timeout=PAGE_GOTO_TIMEOUT_MS
                )
            except Exception as e:
                log.info("bing pw goto: %s", e)
                break
            try:
                await page.wait_for_selector(
                    "li.b_algo, .b_no, #b_results", timeout=10_000
                )
            except PWTimeoutError:
                pass
            await _human_scroll(page)
            html = await page.content()
            if "captcha" in html.lower() and ("verify" in html.lower()
                                              or "robot" in html.lower()):
                log.info("bing pw captcha for %r", query)
                break
            doms = _domains_from_text(html)
            if not doms:
                break
            out.extend(doms)
            if p < pages - 1:
                await _human_pause()
    finally:
        try:
            await page.close()
        except Exception:
            pass
    return _dedup(out)


async def _bing_search(query: str, ctx: BrowserContext) -> list[str]:
    res = await _bing_via_cffi(query)
    if len(res) >= 5:
        return res
    pw = await _bing_via_playwright(query, ctx)
    return _dedup(res + pw)


# ──────────────────────────────────────────────────────────────────────
# DuckDuckGo


async def _ddg_via_cffi(query: str) -> list[str]:
    html = await _cffi_post(
        "https://html.duckduckgo.com/html/",
        data={"q": query, "kl": "by-ru", "kp": "-2"},
    )
    if not html:
        return []
    if "anomaly" in html[:500].lower():
        log.info("ddg cffi anomaly for %r", query)
        return []
    return _domains_from_text(html)


async def _ddg_via_playwright(query: str, ctx: BrowserContext) -> list[str]:
    page = await _new_search_page(ctx)
    out: list[str] = []
    try:
        for url in (
            f"https://html.duckduckgo.com/html/?q={quote_plus(query)}&kl=by-ru",
            f"https://duckduckgo.com/?q={quote_plus(query)}&kl=by-ru&t=h_",
        ):
            try:
                await page.goto(
                    url, wait_until="domcontentloaded", timeout=15_000
                )
            except Exception as e:
                log.info("ddg pw goto: %s", e)
                continue
            try:
                await page.wait_for_selector(
                    'a.result__a, [data-testid="result-title-a"], '
                    'a.result-link',
                    timeout=8_000,
                )
            except PWTimeoutError:
                continue
            await _human_scroll(page)
            html = await page.content()
            doms = _domains_from_text(html)
            if doms:
                out.extend(doms)
                break
    finally:
        try:
            await page.close()
        except Exception:
            pass
    return _dedup(out)


async def _ddg_search(query: str, ctx: BrowserContext) -> list[str]:
    res = await _ddg_via_cffi(query)
    if len(res) >= 5:
        return res
    pw = await _ddg_via_playwright(query, ctx)
    return _dedup(res + pw)


# ──────────────────────────────────────────────────────────────────────
# Yandex (опционально, часто капча)


async def _yandex_via_cffi(query: str) -> list[str]:
    url = f"https://yandex.by/search/?text={quote_plus(query)}&lr=157"
    html = await _cffi_get(url, referer="https://yandex.by/")
    if not html or "captcha" in html.lower() or "не робот" in html.lower():
        return []
    return _domains_from_text(html)


# ──────────────────────────────────────────────────────────────────────


async def search_query(
    query: str,
    ctx: BrowserContext,
    *,
    min_target: int = 25,
) -> tuple[list[str], str]:
    """Run query through Bing / DDG / Yandex with stealth + TLS impersonation."""
    sources: list[str] = []
    all_domains: list[str] = []

    bing = await _bing_search(query, ctx)
    if bing:
        sources.append(f"bing:{len(bing)}")
        all_domains.extend(bing)

    if len(_dedup(all_domains)) < min_target:
        ddg = await _ddg_search(query, ctx)
        if ddg:
            sources.append(f"ddg:{len(ddg)}")
            all_domains.extend(ddg)

    if len(_dedup(all_domains)) < min_target:
        ya = await _yandex_via_cffi(query)
        if ya:
            sources.append(f"ya:{len(ya)}")
            all_domains.extend(ya)

    final = _dedup(all_domains)
    if not final:
        return [], "empty"
    return final, ",".join(sources)
