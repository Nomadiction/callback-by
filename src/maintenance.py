"""Общие функции обслуживания списка сайтов: верификация и рост.

Используются и из Streamlit UI, и из CLI-скриптов в tools/.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable

import httpx

from playwright.async_api import Error as PWError

try:
    from playwright.async_api import TargetClosedError  # playwright >=1.40
except ImportError:  # older playwright
    TargetClosedError = PWError  # type: ignore[misc,assignment]

from src.browser import browser_context
from src.config import RunSettings, load_keywords, load_sites, save_sites
from src.discovery import (
    EXCLUDE_DOMAINS,
    _domain_root,
    search_query,
)
from src.exclusions import add_many_with_ts as exclude_many_with_ts, excluded_domains
from tools.verifier import VerifyResult, verify_url

log = logging.getLogger(__name__)


def _silence_orphan_target_closed(loop, ctx):
    exc = ctx.get("exception")
    if isinstance(exc, (TargetClosedError, PWError)):
        return
    msg = ctx.get("message", "")
    if "Target page, context or browser has been closed" in msg:
        return
    loop.default_exception_handler(ctx)

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
    test_submission: bool = False,
    submission_settings: RunSettings | None = None,
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
                    res = await verify_url(
                        entry["url"],
                        ctx,
                        client,
                        test_submission=test_submission,
                        submission_settings=submission_settings,
                    )
                    counter["i"] += 1
                    pairs.append((entry, res))
                    if on_progress is not None:
                        await _maybe_await(on_progress(counter["i"], total, entry, res))

            await asyncio.gather(*[_one(e) for e in sites])

    by_url = {(e["url"]).rstrip("/"): r for e, r in pairs}
    new_list: list[dict] = []
    to_exclude: list[tuple[str, str, str | None]] = []
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
                to_exclude.append(
                    (domain, res.reason or "verification failed", getattr(res, "timestamp", None))
                )

    if save:
        save_sites(new_list)
        exclude_many_with_ts(to_exclude)
    return new_list, pairs


async def grow(
    *,
    target: int = 300,
    headless: bool = True,
    concurrency: int = 3,
    on_progress: GrowProgress | None = None,
    dry_run: bool = False,
    max_empty_rounds: int = 15,
    browser_recycle_every: int = 60,
    max_query_passes: int = 3,
) -> dict:
    """Grow sites.json via DDG/Bing discovery + verification.

    on_progress(event, payload) — events: query_start, query_done,
    candidate_good, candidate_bad, browser_recycled, finished.
    """
    try:
        asyncio.get_running_loop().set_exception_handler(
            _silence_orphan_target_closed
        )
    except Exception:
        pass

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
    candidates_since_recycle = 0
    stop_reason = "queries exhausted"

    async def _emit(event: str, payload: dict):
        if on_progress is not None:
            await _maybe_await(on_progress(event, payload))

    verify_client = httpx.AsyncClient(follow_redirects=True)
    ctx_mgr = browser_context(headless=headless)
    ctx = await ctx_mgr.__aenter__()

    async def _recycle_browser():
        nonlocal ctx, ctx_mgr, candidates_since_recycle
        try:
            await ctx_mgr.__aexit__(None, None, None)
        except Exception:
            log.exception("browser context teardown failed")
        ctx_mgr = browser_context(headless=headless)
        ctx = await ctx_mgr.__aenter__()
        candidates_since_recycle = 0
        await _emit("browser_recycled", {"after": browser_recycle_every})

    try:
        for pass_idx in range(max_query_passes):
            pass_new_good = 0
            for q_idx, q in enumerate(queries):
                if initial_good + new_good >= target:
                    stop_reason = "target reached"
                    break

                if q_idx > 0 or pass_idx > 0:
                    await asyncio.sleep(1.0)

                await _emit("query_start", {
                    "query": q,
                    "current": initial_good + new_good,
                    "target": target,
                    "q_idx": q_idx + 1,
                    "q_total": len(queries),
                    "pass_idx": pass_idx + 1,
                    "pass_total": max_query_passes,
                })

                try:
                    domains, source = await search_query(q, ctx)
                except Exception as e:
                    log.exception("search failed for %r", q)
                    await _emit("query_done", {
                        "query": q,
                        "candidates": 0,
                        "empty_rounds": empty_rounds + 1,
                        "source": "error",
                        "error": str(e),
                        "pass_idx": pass_idx + 1,
                    })
                    empty_rounds += 1
                    if empty_rounds >= max_empty_rounds:
                        stop_reason = "too many empty rounds"
                        break
                    continue

                raw_total = len(domains)
                already_known = 0
                already_excluded = 0
                fresh: list[str] = []
                for d in domains:
                    if d in EXCLUDE_DOMAINS:
                        continue
                    if d in known:
                        already_known += 1
                        continue
                    if d in excluded:
                        already_excluded += 1
                        skipped_excluded += 1
                        continue
                    fresh.append(d)

                if not fresh:
                    if raw_total == 0:
                        reason = "search returned 0"
                    else:
                        reason = (
                            f"all dups: {already_known} known, "
                            f"{already_excluded} excluded"
                        )
                    empty_rounds += 1
                    await _emit("query_done", {
                        "query": q,
                        "candidates": 0,
                        "raw_total": raw_total,
                        "already_known": already_known,
                        "already_excluded": already_excluded,
                        "empty_rounds": empty_rounds,
                        "source": source,
                        "reason": reason,
                        "pass_idx": pass_idx + 1,
                    })
                    if empty_rounds >= max_empty_rounds:
                        stop_reason = "too many empty rounds"
                        break
                    continue
                empty_rounds = 0
                await _emit("query_done", {
                    "query": q,
                    "candidates": len(fresh),
                    "raw_total": raw_total,
                    "already_known": already_known,
                    "already_excluded": already_excluded,
                    "empty_rounds": 0,
                    "source": source,
                    "pass_idx": pass_idx + 1,
                })

                async def _check(domain: str):
                    async with sem:
                        url = f"https://{domain}"
                        try:
                            res = await asyncio.wait_for(
                                verify_url(url, ctx, verify_client),
                                timeout=60.0,
                            )
                        except asyncio.TimeoutError:
                            from tools.verifier import VerifyResult
                            res = VerifyResult(url=url, reason="hard timeout")
                        except Exception as e:
                            from tools.verifier import VerifyResult
                            log.exception("verify crashed for %s", url)
                            res = VerifyResult(
                                url=url,
                                reason=f"{type(e).__name__}: {e}",
                            )
                        return domain, res

                checks = await asyncio.gather(
                    *[_check(d) for d in fresh],
                    return_exceptions=False,
                )
                round_excludes: list[tuple[str, str]] = []
                target_reached = False
                for domain, res in checks:
                    known.add(domain)
                    candidates_since_recycle += 1
                    if res.good:
                        entry = {
                            "url": f"https://{domain}",
                            "category": "discovered",
                            "enabled": True,
                        }
                        if not dry_run:
                            sites.append(entry)
                        new_good += 1
                        pass_new_good += 1
                        await _emit("candidate_good", {
                            "domain": domain,
                            "current": initial_good + new_good,
                            "target": target,
                            "pass_idx": pass_idx + 1,
                        })
                        if initial_good + new_good >= target:
                            target_reached = True
                            stop_reason = "target reached"
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
                            {"domain": domain, "reason": res.reason, "pass_idx": pass_idx + 1},
                        )

                if not dry_run:
                    save_sites(sites)
                    exclude_many(round_excludes)

                if target_reached:
                    break

                if candidates_since_recycle >= browser_recycle_every:
                    await _recycle_browser()

            if initial_good + new_good >= target or stop_reason != "queries exhausted":
                break
            if pass_new_good == 0:
                stop_reason = "full pass without new sites"
                break
    finally:
        try:
            await ctx_mgr.__aexit__(None, None, None)
        except Exception:
            log.exception("final browser teardown failed")
        await verify_client.aclose()

    summary = {
        "new_good": new_good,
        "skipped": skipped,
        "skipped_excluded": skipped_excluded,
        "total": len(sites),
        "stop_reason": stop_reason,
        "target": target,
        "current": initial_good + new_good,
        "max_query_passes": max_query_passes,
    }
    await _emit("finished", summary)
    return summary
