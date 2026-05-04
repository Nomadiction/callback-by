"""Поиск .by-кандидатов: httpx + BeautifulSoup, без Playwright.

Старый Playwright-скрап на duckduckgo.com был хрупким: страница часто
показывала анти-бот заглушку и DDG возвращал лишь header/footer-ссылки.

Новый подход:
1. POST `https://html.duckduckgo.com/html/` (lite-версия, без JS).
2. Если получен 202 (challenge) — экспоненциальный backoff и retry.
3. Если N попыток подряд провалились — fallback на Bing HTML.
4. Парсим с BeautifulSoup, фильтруем по `.by` и эвристикам исключений.
"""
from __future__ import annotations

import asyncio
import logging
import random
import re
from typing import Iterable
from urllib.parse import parse_qs, unquote, urlparse

import httpx
from bs4 import BeautifulSoup

log = logging.getLogger(__name__)

DDG_HTML_URL = "https://html.duckduckgo.com/html/"
DDG_LITE_URL = "https://lite.duckduckgo.com/lite/"
BING_URL = "https://www.bing.com/search"

DDG_URL = "https://duckduckgo.com/"

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:122.0) Gecko/20100101 "
    "Firefox/122.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.3 Safari/605.1.15",
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
}


def _extract_real_url(href: str) -> str | None:
    """DDG HTML отдаёт редиректы вида /l/?uddg=<encoded>. Возвращает реальный URL."""
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
    return netloc


def _dedup(items: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for x in items:
        if x and x not in seen:
            seen.add(x)
            out.append(x)
    return out


async def _ddg_html(
    query: str,
    client: httpx.AsyncClient,
    *,
    max_retries: int = 3,
) -> list[str]:
    backoff = 2.0
    for attempt in range(max_retries):
        try:
            resp = await client.post(
                DDG_HTML_URL,
                data={"q": query, "kl": "by-ru", "kp": "-2"},
                headers={**HEADERS, "User-Agent": random.choice(USER_AGENTS)},
                timeout=20.0,
            )
        except (httpx.RequestError, httpx.TimeoutException) as e:
            log.warning("ddg request failed (attempt %d): %s", attempt + 1, e)
            await asyncio.sleep(backoff)
            backoff *= 1.7
            continue

        if resp.status_code == 202 or "anomaly" in (resp.text[:200].lower()):
            log.info("ddg challenge for %r — backoff %.1fs", query, backoff)
            await asyncio.sleep(backoff)
            backoff *= 1.7
            continue

        if resp.status_code != 200:
            log.warning("ddg http %s for %r", resp.status_code, query)
            await asyncio.sleep(backoff)
            backoff *= 1.5
            continue

        return _parse_ddg(resp.text)

    return []


def _parse_ddg(html: str) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    domains: list[str] = []
    for a in soup.select("a.result__a, a.result__url, a.result-link"):
        href = a.get("href") or ""
        real = _extract_real_url(href)
        if real:
            d = _domain_root(real)
            if d:
                domains.append(d)
    if not domains:
        for a in soup.find_all("a"):
            href = a.get("href") or ""
            if "uddg=" not in href and not href.startswith("http"):
                continue
            real = _extract_real_url(href)
            if real:
                d = _domain_root(real)
                if d:
                    domains.append(d)
    return _dedup(domains)


async def _bing_html(
    query: str,
    client: httpx.AsyncClient,
    *,
    max_retries: int = 2,
) -> list[str]:
    backoff = 1.5
    for attempt in range(max_retries):
        try:
            resp = await client.get(
                BING_URL,
                params={"q": query, "setlang": "ru", "cc": "by", "count": "50"},
                headers={**HEADERS, "User-Agent": random.choice(USER_AGENTS)},
                timeout=20.0,
            )
        except (httpx.RequestError, httpx.TimeoutException) as e:
            log.warning("bing request failed (attempt %d): %s", attempt + 1, e)
            await asyncio.sleep(backoff)
            backoff *= 1.5
            continue
        if resp.status_code != 200:
            log.warning("bing http %s for %r", resp.status_code, query)
            await asyncio.sleep(backoff)
            backoff *= 1.5
            continue
        return _parse_bing(resp.text)
    return []


def _parse_bing(html: str) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    domains: list[str] = []
    for sel in ("li.b_algo h2 a", "li.b_algo cite", "h2 a"):
        for a in soup.select(sel):
            href = a.get("href") or a.get_text(strip=True) or ""
            if not href.startswith(("http://", "https://")):
                href = "https://" + href
            d = _domain_root(href)
            if d:
                domains.append(d)
    return _dedup(domains)


async def search_query(
    query: str,
    client: httpx.AsyncClient,
) -> tuple[list[str], str]:
    """Search a single query. Returns (domains, source).

    source ∈ {"ddg", "bing", "empty"}.
    """
    domains = await _ddg_html(query, client)
    if domains:
        return domains, "ddg"
    log.info("ddg empty for %r — trying bing", query)
    domains = await _bing_html(query, client)
    if domains:
        return domains, "bing"
    return [], "empty"
