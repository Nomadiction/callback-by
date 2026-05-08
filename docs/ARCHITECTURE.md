# Архитектура

## Слои

```
┌─────────────────────────────────────────────────────────┐
│ app.py (Streamlit UI)                                   │
│  - 3 таба: Рассылка / Сайты / Обслуживание              │
│  - редактор data/sites.json (фильтр, edit, bulk-ops)    │
│  - запуск pipeline / verify / grow в worker-thread      │
│  - стрим прогресса через queue.Queue + add_script_run_ctx│
│  - live-таблица результатов + CSV-download              │
└──────┬──────────────────────────────┬───────────────────┘
       │ Рассылка (RunSettings + URLs)│ Обслуживание
       ▼                              ▼
┌─────────────────────┐      ┌──────────────────────────┐
│ src/runner.py       │      │ src/maintenance.py       │
│  - последовательно  │      │  - verify_all (sites.json)│
│  - rate-limit       │      │  - grow (DDG + verify)   │
│  - per-site timeout │      │  - on_progress callbacks │
└────────┬────────────┘      └─────────┬────────────────┘
         │ Page (Playwright)           │
         │                             │ verify_url
         ▼                             ▼
┌────────────┐    ┌──────────────┐   ┌──────────────────┐
│ form_finder│───►│ form_filler  │   │ tools/verifier.py│
│  - find    │    │  - submit    │   │  - alive + form  │
└────────────┘    └──────────────┘   └──────────────────┘
       ▲                                     │
       └─────────────────┬───────────────────┘
                         │ Browser context
                         ▼
                ┌─────────────────────────┐
                │ src/browser.py          │
                │  - async Playwright ctx │
                └─────────────────────────┘
```

## Потоки данных

### Рассылка
1. UI собирает `RunSettings` (phone, message, pause, headless, max_sites) и список включённых URL из `data/sites.json`.
2. Worker-thread с `asyncio.new_event_loop()` вызывает `runner.run(...)`.
3. На каждый сайт: `browser.browser_context` → новая `Page` → `form_finder.find_form` → `form_filler.submit_form` → `SiteResult` → `on_progress(res)` → live-таблица.
4. Финал: все `SiteResult` пишутся в `logs/results_<timestamp>.csv` (UTF-8 BOM).

### Верификация и рост (`src.maintenance`)
- **`verify_all`** — для каждого сайта вызывает `tools.verifier.verify_url`: базово это HTTP-probe + поиск callback-формы, а в строгом режиме ещё и тест ввода номера с попыткой submit. Нерабочие сайты удаляются из `sites.json` и переносятся в `data/excluded_domains.json` через `src.exclusions.add_many`.
- **`grow`** — поиск по `keywords.json::discovery_queries` через `src.discovery.search_query`: сначала `html.duckduckgo.com/html/` (httpx + BeautifulSoup, exponential backoff на 202), при пустом результате — фоллбэк на Bing HTML. Кандидаты, уже попавшие в исключения, пропускаются; новые верифицируются и попадают в `sites.json` только если живы и форма найдена. Отбракованные тут же дописываются в исключения.

### Чёрный список (`src.exclusions`)
- Хранилище `data/excluded_domains.json`: `{domain: {reason, ts}}`.
- API: `add_many`, `remove`, `clear`, `excluded_domains() -> set[str]`, `load_exclusions() -> dict`.
- Управление в UI — таб «Обслуживание» → панель «🚫 Список исключений» (фильтр, разблокировка одного домена, очистка всего списка).
- CLI-эквивалент: `python -m tools.verify_sites --rerun-excluded`.

## Модули

| Модуль | Роль |
| --- | --- |
| `app.py` | Streamlit UI, общая обвязка async + threading |
| `src/config.py` | Пути, `RunSettings`, IO для `sites.json` / `keywords.json` |
| `src/phone.py` | Нормализация номера + 5 input-mask вариантов |
| `src/browser.py` | `browser_context()` async-CM с Chromium |
| `src/form_finder.py` | Двухступенчатый поиск формы (прямая → клик-модалка) |
| `src/form_filler.py` | Заполнение телефона/сообщения, submit, success-детект |
| `src/discovery.py` | Поиск через DDG HTML + Bing fallback, парсеры URL/доменов, EXCLUDE_DOMAINS |
| `src/exclusions.py` | CRUD чёрного списка доменов |
| `src/maintenance.py` | `verify_all`, `grow` — общая логика для UI и CLI |
| `src/runner.py` | Оркестратор рассылки (по сайтам, паузы, сбор `SiteResult`) |
| `src/result.py` | `SiteResult` + `Status` enum + CSV-writer |
| `tools/verifier.py` | `verify_url` (HTTP probe + form probe, optional strict submit test), общий движок верификации |
| `tools/verify_sites.py` | CLI-обёртка над `verify_all` |
| `tools/grow_list.py` | CLI-обёртка над `grow` |

## Расширение

- **Триггеры формы / hints** → `data/keywords.json` (`form_triggers`, `phone_field_hints`, `message_field_hints`, `submit_button_hints`).
- **Success-индикаторы** → `keywords.json::success_indicators`.
- **Запросы автопоиска** → `keywords.json::discovery_queries`.
- **Эвристики поиска** → `src/form_finder.py` (iframe-виджеты JivoSite/Bitrix24 пока не покрыты).

## Известные ограничения

- iframe-виджеты (JivoSite, Bitrix24-чат) не обрабатываются — нужен обход `page.frames`.
- Капча (reCAPTCHA, hCaptcha) ломает submit. Помечается `SUBMITTED`, но запрос не уходит.
- Cloudflare Challenge → результат `UNREACHABLE`.
