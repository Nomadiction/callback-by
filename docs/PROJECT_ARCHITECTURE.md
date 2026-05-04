# Project Architecture

> This document is the single source of truth for how the project is structured and how core flows work. If code changes, update this file to keep it accurate.

## Purpose

Callback BY is a Streamlit app that automates submitting "callback / request a call" forms on Belarusian (.by) websites. It uses Playwright (Chromium) for browser automation and optionally grows the site list via DuckDuckGo HTML search.

## Directory Layout

```
app.py
README.md
requirements.txt
data/
  excluded_domains.json
  keywords.json
  sites.json
docs/
  ARCHITECTURE.md
  FORM_HEURISTICS.md
  SITES.md
  PROJECT_ARCHITECTURE.md  <-- this file
logs/
src/
  __init__.py
  browser.py
  config.py
  discovery.py
  exclusions.py
  form_filler.py
  form_finder.py
  maintenance.py
  phone.py
  result.py
  runner.py
tools/
  __init__.py
  grow_list.py
  verifier.py
  verify_sites.py
```

## High-Level Layers

```
Streamlit UI (app.py)
  -> Runner (src/runner.py) for sending forms
  -> Maintenance (src/maintenance.py) for verify/grow
  -> Results/logs (src/result.py, logs/)

Core browser automation
  -> Browser context (src/browser.py)
  -> Form finding (src/form_finder.py)
  -> Form filling (src/form_filler.py)

Support services
  -> Config/data IO (src/config.py)
  -> Discovery helpers (src/discovery.py)
  -> Exclusions list (src/exclusions.py)
  -> CLI wrappers (tools/*.py)
```

## Main Runtime Flows

### 1) Sending Forms (UI)

1. User configures settings in Streamlit (phone, message, pause, headless, max sites).
2. `app.py` collects enabled URLs from `data/sites.json`.
3. A worker thread creates an asyncio loop and calls `src.runner.run()`.
4. For each site:
   - Create a new Playwright page from `src.browser.browser_context()`.
   - Navigate to the URL.
   - `src.form_finder.find_form()` locates the callback form.
   - `src.form_filler.submit_form()` fills phone/message and submits.
   - A `SiteResult` is emitted via `on_progress` to live-update the UI.
5. Results are written to `logs/results_<timestamp>.csv` with UTF-8 BOM.

### 2) Verify Existing Sites

Used from both UI and CLI.

- `src.maintenance.verify_all()` checks each site with `tools.verifier.verify_url()`:
  - HTTP probe (alive?)
  - Playwright form probe (form found?)
- Bad sites are removed from `data/sites.json` and appended to `data/excluded_domains.json`.

CLI entry point: `python -m tools.verify_sites`.

### 3) Grow Site List (Discovery)

- `src.maintenance.grow()` iterates queries from `data/keywords.json::discovery_queries`.
- Each query runs through `src.discovery.search_query`:
  1. POST to `https://html.duckduckgo.com/html/` (httpx + BeautifulSoup, no Playwright).
  2. On 202/anti-bot: exponential backoff and retry.
  3. If DDG stays empty: fallback to Bing HTML (`https://www.bing.com/search`).
- Each new domain is verified immediately via `tools.verifier.verify_url`.
- Only verified domains are appended to `data/sites.json`.
- Failed domains are appended to `data/excluded_domains.json`.

CLI entry point: `python -m tools.grow_list`.

## Core Modules

### UI

- `app.py`
  - Streamlit UI with 3 tabs: Send / Sites / Maintenance
  - Spawns worker threads for async flows
  - Displays live progress, results table, CSV download

### Orchestration

- `src.runner.py`
  - Sequential processing of websites
  - Per-site timeout, random pause, progress callback

- `src.maintenance.py`
  - `verify_all()` for bulk verification
  - `grow()` for discovery + immediate verification

### Browser Automation

- `src.browser.py`
  - Playwright Chromium context with stable UA and viewport

- `src.form_finder.py`
  - Two-stage form detection:
    1) Try to find phone input directly
    2) Click "callback" triggers, then search again

- `src.form_filler.py`
  - Phone fill using 5 format variants
  - Optional message fill
  - Submit click and success detection

### Data & Config

- `src.config.py`
  - File paths, `RunSettings`, JSON IO for sites/keywords

- `src.discovery.py`
  - DDG HTML URL parsing and domain filtering

- `src.exclusions.py`
  - CRUD for `data/excluded_domains.json`

- `src.result.py`
  - `SiteResult`, `Status`, CSV writer

### CLI Tools

- `tools/verifier.py`: core verification engine
- `tools/verify_sites.py`: CLI wrapper for verify
- `tools/grow_list.py`: CLI wrapper for grow

## Data Files

### `data/sites.json`

List of site entries:

```json
{"url": "https://example.by", "category": "marketplace", "enabled": true}
```

- `url`: full URL, including protocol
- `category`: grouping label for UI filtering
- `enabled`: whether the site is in the send queue

### `data/excluded_domains.json`

Map of domain to reason and timestamp:

```json
{
  "example.by": {"reason": "no callback form", "ts": "2026-05-03T12:00:00"}
}
```

### `data/keywords.json`

Configuration lists used by heuristics and discovery:

- `form_triggers`
- `phone_field_hints`
- `message_field_hints`
- `submit_button_hints`
- `success_indicators`
- `discovery_queries`

## Form Detection Logic (Summary)

See docs/FORM_HEURISTICS.md for details.

- Find a visible phone input with selectors based on `phone_field_hints`.
- If not found, click elements with text from `form_triggers` and retry.
- Resolve container and submit button.
- Fill phone using variants; optional message field.
- Detect success by scanning body text for `success_indicators`.

## Logging and Output

- `logs/run.log`: structured app log
- `logs/results_<timestamp>.csv`: per-run results

## Known Limitations

- Iframe widgets (e.g., JivoSite, Bitrix24) are not handled.
- CAPTCHA blocks submissions and yields false positives.
- Cloudflare challenges often appear as unreachable.

## Maintenance Rules (Keep This Doc Accurate)

When you change code, check and update:

- This file (docs/PROJECT_ARCHITECTURE.md)
- docs/ARCHITECTURE.md
- docs/FORM_HEURISTICS.md
- docs/SITES.md

This keeps the documentation consistent with the implementation.
