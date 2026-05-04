"""Общие функции обслуживания списка сайтов: верификация и рост.

Используются и из Streamlit UI, и из CLI-скриптов в tools/.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable

import httpx

from src.browser import browser_context
from src.config import load_keywords, load_sites, save_sites
from src.discovery import (
    EXCLUDE_DOMAINS,
    HEADERS,
    _domain_root,
    search_query,
)
from src.exclusions import add_many as exclude_many, excluded_domains
from tools.verifier import VerifyResult, verify_url

log = logging.getLogger(__name__)

VerifyProgress = Callable[[int, int, dict, VerifyResult], "Awaitable[None] | None"]
GrowProgress = Callable[[str, dict], "Awaitable[None] | None"]


async def _maybe_await(maybe):
    if asyncio.iscoroutine(maybe):
        await maybe


async def verify_all(
    sites: list[dict] | None = None,
    *,
    headless: bool = True,
    concurrency: int = 3,
    on_progress: VerifyProgress | None = None,
    save: bool = True,
) -> tuple[list[dict], list[tuple[dict, VerifyResult]]]:
    """Verify each site.

    Unusable sites (no form / dead) удаляются и попадают в exclusions.

    Returns (new_list, pairs). new_list — то, что осталось в sites.json.
    """
    if sites is None:
        sites = load_sites()
    pairs: list[tuple[dict, VerifyResult]] = []
    sem = asyncio.Semaphore(concurrency)
    total = len(sites)
    counter = {"i": 0}

    async with browser_context(headless=headless) as ctx:
        async with httpx.AsyncClient(follow_redirects=True) as client:
            async def _one(entry: dict):
                async with sem:
                    res = await verify_url(entry["url"], ctx, client)
                    counter["i"] += 1
                    pairs.append((entry, res))
                    if on_progress is not None:
                        await _maybe_await(on_progress(counter["i"], total, entry, res))

            await asyncio.gather(*[_one(e) for e in sites])

    by_url = {(e["url"]).rstrip("/"): r for e, r in pairs}
    new_list: list[dict] = []
    to_exclude: list[tuple[str, str]] = []
    for entry in sites:
        key = entry["url"].rstrip("/")
        res = by_url.get(key)
        if res is None:
            new_list.append(entry)
            continue
        if res.good:
            entry = {**entry, "enabled": True}
            entry.pop("last_check_reason", None)
            new_list.append(entry)
        else:
            domain = _domain_root(entry["url"])
            if domain:
                to_exclude.append((domain, res.reason or "verification failed"))

    if save:
        save_sites(new_list)
        exclude_many(to_exclude)
    return new_list, pairs


async def grow(
    *,
    target: int = 300,
    headless: bool = True,
    concurrency: int = 3,
    on_progress: GrowProgress | None = None,
    dry_run: bool = False,
    max_empty_rounds: int = 5,
) -> dict:
    """Grow sites.json via DDG/Bing discovery + verification.

    on_progress(event, payload) — events: query_start, query_done,
    candidate_good, candidate_bad, finished.
    """
    sites = load_sites()
    known: set[str] = set()
    for s in sites:
        d = _domain_root(s["url"])
        if d:
            known.add(d)
    excluded = excluded_domains()
    initial_good = sum(1 for s in sites if s.get("enabled"))
    queries = load_keywords()["discovery_queries"]
    sem = asyncio.Semaphore(concurrency)
    new_good = 0
    skipped = 0
    skipped_excluded = 0
    empty_rounds = 0

    async def _emit(event: str, payload: dict):
        if on_progress is not None:
            await _maybe_await(on_progress(event, payload))

    search_client = httpx.AsyncClient(
        follow_redirects=True,
        headers=HEADERS,
        timeout=30.0,
    )

    try:
        async with browser_context(headless=headless) as ctx:
            async with httpx.AsyncClient(follow_redirects=True) as verify_client:
                for q_idx, q in enumerate(queries):
                    if initial_good + new_good >= target:
                        break

                    if q_idx > 0:
                        await asyncio.sleep(1.0)

                    await _emit("query_start", {
                        "query": q,
                        "current": initial_good + new_good,
                        "target": target,
                        "q_idx": q_idx + 1,
                        "q_total": len(queries),
                    })

                    try:
                        domains, source = await search_query(q, search_client)
                    except Exception as e:
                        log.exception("search failed for %r", q)
                        await _emit("query_done", {
                            "query": q,
                            "candidates": 0,
                            "empty_rounds": empty_rounds + 1,
                            "source": "error",
                            "error": str(e),
                        })
                        empty_rounds += 1
                        if empty_rounds >= max_empty_rounds:
                            break
                        continue

                    fresh: list[str] = []
                    for d in domains:
                        if d in known or d in EXCLUDE_DOMAINS:
                            continue
                        if d in excluded:
                            skipped_excluded += 1
                            continue
                        fresh.append(d)

                    if not fresh:
                        empty_rounds += 1
                        await _emit("query_done", {
                            "query": q,
                            "candidates": 0,
                            "empty_rounds": empty_rounds,
                            "source": source,
                        })
                        if empty_rounds >= max_empty_rounds:
                            break
                        continue
                    empty_rounds = 0
                    await _emit("query_done", {
                        "query": q,
                        "candidates": len(fresh),
                        "empty_rounds": 0,
                        "source": source,
                    })

                    async def _check(domain: str):
                        async with sem:
                            url = f"https://{domain}"
                            res = await verify_url(url, ctx, verify_client)
                            return domain, res

                    checks = await asyncio.gather(*[_check(d) for d in fresh])
                    round_excludes: list[tuple[str, str]] = []
                    for domain, res in checks:
                        known.add(domain)
                        if res.good:
                            entry = {
                                "url": f"https://{domain}",
                                "category": "discovered",
                                "enabled": True,
                            }
                            if not dry_run:
                                sites.append(entry)
                            new_good += 1
                            await _emit("candidate_good", {
                                "domain": domain,
                                "current": initial_good + new_good,
                                "target": target,
                            })
                            if initial_good + new_good >= target:
                                break
                        else:
                            skipped += 1
                            if not dry_run:
                                excluded.add(domain)
                                round_excludes.append(
                                    (domain, res.reason or "verification failed")
                                )
                            await _emit(
                                "candidate_bad",
                                {"domain": domain, "reason": res.reason},
                            )

                    if not dry_run:
                        save_sites(sites)
                        exclude_many(round_excludes)
    finally:
        await search_client.aclose()

    summary = {
        "new_good": new_good,
        "skipped": skipped,
        "skipped_excluded": skipped_excluded,
        "total": len(sites),
    }
    await _emit("finished", summary)
    return summary
