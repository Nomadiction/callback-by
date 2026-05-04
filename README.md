# Callback BY

Streamlit-приложение для массовой отправки форм «Перезвонить / Обратный звонок» на белорусских (`.by`) сайтах. UI на Streamlit, автоматизация — Playwright (Chromium), опциональный автопоиск новых сайтов через DuckDuckGo HTML.

## Установка (один раз)

Требуется **Python 3.12+**.

Рекомендуемый способ — проектный виртуальный env `.venv` и запуск **всех команд через него** (на Windows это избавляет от проблем, когда в системе несколько Python).

### Linux / macOS

```bash
# 1. (опционально) изолированное окружение, чтобы не засорять систему
python3 -m venv .venv
source .venv/bin/activate

# 2. поставить зависимости (streamlit, playwright, httpx, bs4, pandas)
pip install -r requirements.txt

# 3. скачать бинарник Chromium для Playwright (~150 MB, один раз)
playwright install chromium
```

### Windows (PowerShell)

```powershell
# 1) создать виртуальное окружение (один раз)
py -3.12 -m venv .venv

# 2) поставить зависимости в .venv
.\.venv\Scripts\python.exe -m pip install -U pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt

# 3) скачать Chromium для Playwright (обязательно, один раз)
.\.venv\Scripts\python.exe -m playwright install chromium
```

Примечания:
- Если `py -3.12` не находится, посмотри доступные версии: `py -0p`.
- Не смешивай `pip`/`python` из разных интерпретаторов. Самый надёжный вариант — всегда вызывать `.\.venv\Scripts\python.exe ...`.

---

## Запуск

Все команды запускаются из корня проекта `callback-by/`. Сначала **сделай хотя бы один прогон верификации**, чтобы отсеять мёртвые домены, затем — UI или grow_list.

### 1. Streamlit-UI — основной режим (отправка форм)

```bash
.\.venv\Scripts\python.exe -m streamlit run app.py
```

Что делает: поднимает локальный веб-сервер на `http://localhost:8501`. В UI вводишь номер, выбираешь сайты, жмёшь «Отправить» — таблица результатов обновляется live, CSV-лог пишется в `logs/results_<timestamp>.csv`.

Порядок действий в UI:
1. В сайдбаре — галочка согласия «Я понимаю, что это вызовет реальные звонки».
2. Вбить номер (`+375 29 123 45 67`, либо `80…`, либо 9 цифр).
3. (Опц.) текст в «Сообщение».
4. В разделе «Источники сайтов» — снять галочки с ненужных строк или нажать «🔍 Найти новые сайты сейчас» (если включён автопоиск).
5. Кнопка **«🚀 Отправить на все сайты»** — запуск.

### 2. Верификация существующего списка `sites.json`

```bash
# A. Сухой прогон — только отчёт, файл не трогается
.\.venv\Scripts\python.exe -m tools.verify_sites --dry-run

# B. Полная проверка — удаляет нерабочие сайты, копит в исключения
.\.venv\Scripts\python.exe -m tools.verify_sites --concurrency 4

# C. Принудительная перепроверка ранее отбракованных доменов
.\.venv\Scripts\python.exe -m tools.verify_sites --rerun-excluded --concurrency 4
```

Что делает: для каждого сайта в `data/sites.json` — HTTP GET (жив ли домен) + Playwright/`form_finder` (есть ли callback-форма). Сайты без формы или мёртвые **удаляются из `sites.json`** и записываются в `data/excluded_domains.json` с причиной и датой — при росте списка они пропускаются и не тратят время на повторную проверку. `--concurrency N` — сколько проверок параллельно (3 по умолчанию). `--rerun-excluded` сбрасывает исключения перед запуском.

### 3. Рост списка до целевого числа

```bash
.\.venv\Scripts\python.exe -m tools.grow_list --target 300 --concurrency 4
```

Что делает: крутит DDG-запросы из `data/keywords.json::discovery_queries`, **сразу верифицирует** каждый найденный домен и добавляет в `sites.json` **только подтверждённые** (живые + с реальной формой). Стоп: достигнут `--target` или 3 пустых раунда DDG подряд.

Флаги:
- `--target N` — сколько enabled-сайтов набирать (по умолчанию 300).
- `--concurrency N` — параллельная верификация (по умолчанию 3, можно 4–6).
- `--headed` — показывать окно браузера во время верификации (для отладки).

Запросы намеренно нейтральные (ритейл, услуги, B2B). Медклиники и ритуальные агентства из набора запросов исключены.

### Типичный сценарий «с нуля до боевого прогона»

```bash
# 1. установка (один раз)
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m playwright install chromium

# 2. почистить seed-список от того, что уже сломано (удаляет нерабочие, копит исключения)
.\.venv\Scripts\python.exe -m tools.verify_sites --concurrency 4

# 3. дорастить до целевых 300 живых сайтов
.\.venv\Scripts\python.exe -m tools.grow_list --target 300 --concurrency 4

# 4. запустить UI и отправить
.\.venv\Scripts\python.exe -m streamlit run app.py
```

## Структура

См. [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md). Логика поиска формы — [docs/FORM_HEURISTICS.md](docs/FORM_HEURISTICS.md). Заметки по сайтам — [docs/SITES.md](docs/SITES.md).

## Логи и состояние

- `logs/run.log` — структурированный лог (ошибки, отладка).
- `logs/results_<timestamp>.csv` — результат каждого прогона. UTF-8 with BOM, открывается в Excel без перекодировки.
- `data/sites.json` — рабочий список: только то, что прошло верификацию.
- `data/excluded_domains.json` — отбракованные домены с причиной и датой. Управление — в UI на вкладке «Обслуживание» (фильтр, разблокировка отдельного домена, очистка списка).

## Этика и ограничения

Каждая отправка callback-формы — это реальный звонок оператора компании. Используйте инструмент **только для своих номеров** или с разрешения владельца. Параметры по умолчанию (пауза 2–5 сек, hard-cap 100 сайтов, обязательное согласие) призваны минимизировать злоупотребление.
