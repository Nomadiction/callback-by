from __future__ import annotations

import asyncio
import io
import logging
import queue
import threading
from collections import Counter
from datetime import datetime

import pandas as pd
import streamlit as st

from src.config import LOGS_DIR, RunSettings, load_sites, save_sites
from src.exclusions import (
    clear as clear_exclusions,
    load_exclusions,
    remove as remove_exclusion,
)
from src.maintenance import grow, verify_all
from src.phone import PhoneError, normalize
from src.result import SiteResult, Status, write_csv
from src.runner import run as run_pipeline

try:
    from streamlit.runtime.scriptrunner import add_script_run_ctx, get_script_run_ctx
except Exception:  # старые версии Streamlit
    add_script_run_ctx = None  # type: ignore
    get_script_run_ctx = None  # type: ignore

logging.basicConfig(
    level=logging.INFO,
    filename=LOGS_DIR / "run.log",
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    encoding="utf-8",
)

COLUMNS_RU = {
    "site": "Сайт",
    "form_found": "Форма найдена",
    "phone_sent": "Номер отправлен",
    "message_sent": "Комментарий отправлен",
    "status": "Статус",
    "error": "Ошибка",
    "timestamp": "Время",
}

STATUS_STYLES = {
    Status.SUCCESS.value: ("#5fd189", "rgba(46, 184, 92, 0.18)"),
    Status.SUBMITTED.value: ("#ffce5c", "rgba(255, 193, 7, 0.16)"),
    Status.NO_FORM.value: ("#9aa6b2", "rgba(154, 166, 178, 0.18)"),
    Status.FAILED.value: ("#ee7783", "rgba(220, 53, 69, 0.18)"),
    Status.TIMEOUT.value: ("#f59e0b", "rgba(245, 158, 11, 0.18)"),
    Status.UNREACHABLE.value: ("#a78bfa", "rgba(167, 139, 250, 0.18)"),
}

CUSTOM_CSS = """
<style>
:root {
    --cb-border: rgba(255, 255, 255, 0.08);
    --cb-surface: rgba(255, 255, 255, 0.035);
    --cb-surface-strong: rgba(255, 255, 255, 0.06);
    --cb-text-soft: rgba(230, 232, 235, 0.72);
    --cb-accent: #ff4b4b;
}
.stApp [data-testid="stHeader"] { background: transparent; }
.block-container { padding-top: 1.6rem; padding-bottom: 3rem; }

h1, h2, h3, h4 { letter-spacing: -0.01em; }

[data-testid="stMetric"] {
    background: var(--cb-surface);
    border: 1px solid var(--cb-border);
    border-radius: 12px;
    padding: 14px 18px;
    transition: border-color .15s ease, background .15s ease;
}
[data-testid="stMetric"]:hover { border-color: rgba(255,255,255,0.16); }
[data-testid="stMetricLabel"] { font-weight: 500; color: var(--cb-text-soft); }
[data-testid="stMetricValue"] { font-size: 1.75rem; font-weight: 600; }

.stTabs [data-baseweb="tab-list"] {
    gap: 4px;
    border-bottom: 1px solid var(--cb-border);
    padding-bottom: 2px;
}
.stTabs [data-baseweb="tab"] {
    padding: 10px 18px;
    border-radius: 10px 10px 0 0;
    font-weight: 500;
    color: var(--cb-text-soft);
}
.stTabs [aria-selected="true"] {
    color: #fff;
    background: linear-gradient(180deg, rgba(255,75,75,0.16) 0%, rgba(255,75,75,0.04) 100%);
    border-bottom: 2px solid var(--cb-accent);
}

.stButton > button, .stDownloadButton > button {
    border-radius: 10px;
    font-weight: 500;
}
.stButton > button[kind="primary"] {
    background: linear-gradient(180deg, #ff5b5b 0%, #e64040 100%);
    border: 0;
    box-shadow: 0 1px 0 rgba(0,0,0,0.2), inset 0 1px 0 rgba(255,255,255,0.12);
}
.stButton > button[kind="primary"]:hover {
    filter: brightness(1.05);
    transform: translateY(-1px);
}

div[data-testid="stExpander"] {
    border: 1px solid var(--cb-border);
    border-radius: 12px;
    background: var(--cb-surface);
}

[data-testid="stStatusWidget"] { display: none; }

[data-testid="stDataFrame"] {
    border: 1px solid var(--cb-border);
    border-radius: 12px;
    overflow: hidden;
}

.stProgress > div > div > div { background: var(--cb-accent); }

.cb-pill {
    display: inline-block;
    padding: 3px 10px;
    border-radius: 999px;
    font-size: 0.78rem;
    font-weight: 600;
    line-height: 1.4;
}
.cb-stats {
    display: grid;
    grid-template-columns: repeat(4, minmax(0, 1fr));
    gap: 10px;
    margin: 8px 0 6px;
}
.cb-stat {
    background: var(--cb-surface);
    border: 1px solid var(--cb-border);
    border-radius: 10px;
    padding: 10px 12px;
}
.cb-stat .lbl {
    font-size: 0.72rem;
    color: var(--cb-text-soft);
    text-transform: uppercase;
    letter-spacing: 0.04em;
}
.cb-stat .val {
    font-size: 1.4rem;
    font-weight: 600;
    margin-top: 2px;
}
.cb-stat.good .val { color: #5fd189; }
.cb-stat.bad .val { color: #ee7783; }
.cb-stat.skip .val { color: #9aa6b2; }

.cb-log {
    background: var(--cb-surface);
    border: 1px solid var(--cb-border);
    border-radius: 10px;
    padding: 10px 14px;
    max-height: 360px;
    overflow-y: auto;
    font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
    font-size: 0.82rem;
    line-height: 1.55;
}
.cb-log .ok  { color: #5fd189; }
.cb-log .bad { color: #ee7783; }
.cb-log .neutral { color: var(--cb-text-soft); }
.cb-log .q { color: #ffce5c; font-weight: 600; }

.cb-help {
    color: var(--cb-text-soft);
    font-size: 0.86rem;
    line-height: 1.45;
}
</style>
"""


# ────────────────────────────────────────────────────────────────────
# helpers


def _to_dataframe(results: list[SiteResult]) -> pd.DataFrame:
    rows = [r.as_row() for r in results]
    df = pd.DataFrame(rows, columns=list(COLUMNS_RU.keys()))
    return df.rename(columns=COLUMNS_RU)


def _style_dataframe(df: pd.DataFrame):
    if df.empty:
        return df

    def _row_style(row):
        status = row.get("Статус", "")
        fg, bg = STATUS_STYLES.get(status, ("inherit", "transparent"))
        return [f"background-color: {bg}"] * len(row)

    return df.style.apply(_row_style, axis=1)


def _run_async(coro_factory):
    """Простой run-and-wait: блокирующий запуск async-кода в отдельном потоке."""
    holder: dict = {}

    def _target():
        loop = asyncio.new_event_loop()
        try:
            asyncio.set_event_loop(loop)
            holder["result"] = loop.run_until_complete(coro_factory())
        except Exception as e:
            holder["error"] = e
        finally:
            loop.close()

    t = threading.Thread(target=_target, daemon=True)
    if add_script_run_ctx and get_script_run_ctx:
        try:
            add_script_run_ctx(t, get_script_run_ctx())
        except Exception:
            pass
    t.start()
    t.join()
    if "error" in holder:
        raise holder["error"]
    return holder.get("result")


def _run_async_streaming(coro_factory_with_emit, on_event):
    """Запускает async-функцию в отдельном потоке и стримит события в основной."""
    q: queue.Queue = queue.Queue()
    holder: dict = {}
    DONE = object()

    def _emit(event: str, payload):
        q.put((event, payload))

    async def _wrapper():
        return await coro_factory_with_emit(_emit)

    def _target():
        loop = asyncio.new_event_loop()
        try:
            asyncio.set_event_loop(loop)
            holder["result"] = loop.run_until_complete(_wrapper())
        except Exception as e:
            holder["error"] = e
        finally:
            loop.close()
            q.put((DONE, None))

    t = threading.Thread(target=_target, daemon=True)
    if add_script_run_ctx and get_script_run_ctx:
        try:
            add_script_run_ctx(t, get_script_run_ctx())
        except Exception:
            pass
    t.start()

    while True:
        event, payload = q.get()
        if event is DONE:
            break
        try:
            on_event(event, payload)
        except Exception:
            logging.exception("on_event handler failed")
    t.join()
    if "error" in holder:
        raise holder["error"]
    return holder.get("result")


def _stats(sites: list[dict]) -> dict:
    total = len(sites)
    enabled = sum(1 for s in sites if s.get("enabled"))
    disabled = total - enabled
    cats = Counter(s.get("category", "—") for s in sites)
    return {
        "total": total,
        "enabled": enabled,
        "disabled": disabled,
        "categories": cats,
    }


def _exclusions_count() -> int:
    try:
        return len(load_exclusions())
    except Exception:
        return 0


def _stats_html(stats: dict) -> str:
    cards = [
        ("Прогресс", f'{stats["current"]} / {stats["target"]}', ""),
        ("Добавлено", str(stats["good"]), "good"),
        ("Отсеяно", str(stats["bad"]), "bad"),
        ("Пропущено", str(stats["skipped_excluded"]), "skip"),
    ]
    blocks = "".join(
        f'<div class="cb-stat {cls}">'
        f'<div class="lbl">{lbl}</div><div class="val">{val}</div></div>'
        for lbl, val, cls in cards
    )
    return f'<div class="cb-stats">{blocks}</div>'


# ────────────────────────────────────────────────────────────────────
# sidebar


def render_sidebar() -> tuple[RunSettings, bool]:
    st.sidebar.markdown("### ⚙️ Настройки прогона")

    headless = not st.sidebar.toggle(
        "Показать окно браузера", value=False,
        help="Полезно для отладки, но медленнее",
    )
    pause_min, pause_max = st.sidebar.slider(
        "Пауза между сайтами, сек", 1.0, 10.0, (2.0, 5.0), 0.5,
        help="Случайная пауза снижает нагрузку и риск блокировок",
    )
    max_sites = st.sidebar.number_input(
        "Макс. сайтов за прогон", min_value=1, max_value=300, value=20, step=1,
    )

    st.sidebar.divider()
    st.sidebar.markdown("### 🛠 Параметры обслуживания")
    concurrency = st.sidebar.slider(
        "Параллельных проверок", 1, 8, 3,
        help="Используется в верификации и автопополнении списка",
    )

    st.sidebar.divider()
    st.sidebar.markdown("### ⚠️ Согласие")
    confirmed = st.sidebar.checkbox(
        "Понимаю, что отправка вызовет реальные звонки",
        value=False,
        help="Используй только свои номера или с явного согласия владельца",
    )

    st.sidebar.divider()
    with st.sidebar.expander("ℹ️ О приложении"):
        st.markdown(
            "**Callback BY** — массовая отправка форм обратного звонка "
            "на белорусских сайтах.\n\n"
            "- Streamlit + Playwright\n"
            "- Авторост списка через DDG/Bing\n"
            "- Логи в `logs/`, исключения в `data/excluded_domains.json`",
        )

    settings = RunSettings(
        phone="",
        message="",
        headless=headless,
        pause_min=float(pause_min),
        pause_max=float(pause_max),
        max_sites=int(max_sites),
        discover_new=False,
    )
    setattr(settings, "concurrency", int(concurrency))
    return settings, confirmed


# ────────────────────────────────────────────────────────────────────
# top stats


def render_topbar():
    sites = load_sites()
    s = _stats(sites)
    excl = _exclusions_count()
    cols = st.columns(5)
    cols[0].metric("Всего сайтов", s["total"])
    cols[1].metric("Готовы к отправке", s["enabled"])
    cols[2].metric("Отключены", s["disabled"])
    cols[3].metric("Категорий", len(s["categories"]))
    cols[4].metric(
        "В исключениях",
        excl,
        help="Домены без формы / мёртвые. При росте пропускаются.",
    )


# ────────────────────────────────────────────────────────────────────
# Tab 1: send forms


def render_send_tab(settings: RunSettings, confirmed: bool):
    st.markdown("#### 📞 Параметры рассылки")
    col1, col2 = st.columns([1, 2])
    with col1:
        phone_raw = st.text_input(
            "Номер телефона",
            placeholder="+375 29 123 45 67",
            help="Принимаются форматы +375…, 80…, 9 цифр без кода",
        )
    with col2:
        message = st.text_area(
            "Сообщение (опционально)",
            placeholder="Например: Добрый день, прошу перезвонить для консультации.",
            height=90,
        )

    sites = load_sites()
    enabled = [s for s in sites if s.get("enabled")]
    cats = Counter(s.get("category", "—") for s in enabled)
    cat_filter = st.multiselect(
        "Фильтр по категориям (пусто = все включённые)",
        sorted(cats.keys()),
        default=[],
        help="Выбери, чтобы ограничить рассылку конкретными категориями",
    )
    if cat_filter:
        target_urls = [s["url"] for s in enabled if s.get("category") in cat_filter]
    else:
        target_urls = [s["url"] for s in enabled]

    queue_count = min(len(target_urls), settings.max_sites)
    st.caption(
        f"В очереди: **{queue_count}** из {len(target_urls)} включённых · "
        f"лимит за прогон: {settings.max_sites}"
    )

    if not confirmed:
        st.warning("⚠️ Подтверди согласие в боковой панели, чтобы активировать кнопку.")
    can_run = bool(phone_raw) and bool(target_urls) and confirmed

    if st.button(
        "🚀 Запустить рассылку",
        type="primary",
        disabled=not can_run,
        use_container_width=True,
    ):
        try:
            normalized = normalize(phone_raw)
        except PhoneError as e:
            st.error(f"Некорректный номер: {e}")
            return
        settings.phone = normalized
        settings.message = message
        queue_count = min(len(target_urls), settings.max_sites)
        st.info(
            f"Канонический номер: `{normalized}` · "
            f"сайтов в очереди: {queue_count}"
        )

        progress = st.progress(0.0, text="Старт…")
        live_table = st.empty()
        results_state: list[SiteResult] = []
        total = min(len(target_urls), settings.max_sites)

        def on_progress(res: SiteResult):
            results_state.append(res)
            progress.progress(
                len(results_state) / max(total, 1),
                text=f"{len(results_state)}/{total} · {res.site} → {res.status.value}",
            )
            df = _to_dataframe(results_state)
            live_table.dataframe(
                _style_dataframe(df),
                use_container_width=True,
                hide_index=True,
            )

        try:
            results = _run_async(
                lambda: run_pipeline(target_urls, settings, on_progress=on_progress)
            )
        except Exception as e:
            st.error(f"Ошибка прогона: {e!r}")
            logging.exception("pipeline crash")
            return

        progress.progress(1.0, text="Готово")
        df = _to_dataframe(results)
        live_table.dataframe(
            _style_dataframe(df),
            use_container_width=True,
            hide_index=True,
        )

        statuses = Counter(r.status.value for r in results)
        s_cols = st.columns(min(len(statuses), 6) or 1)
        for i, (k, v) in enumerate(statuses.most_common()):
            s_cols[i % len(s_cols)].metric(k, v)

        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        csv_path = LOGS_DIR / f"results_{ts}.csv"
        write_csv(results, csv_path)

        buf = io.StringIO()
        df.to_csv(buf, index=False, encoding="utf-8-sig")
        st.download_button(
            "⬇️ Скачать CSV",
            data=buf.getvalue().encode("utf-8-sig"),
            file_name=f"results_{ts}.csv",
            mime="text/csv",
        )
        st.toast(f"Лог сохранён: {csv_path.name}", icon="✅")


# ────────────────────────────────────────────────────────────────────
# Tab 2: sites editor


def render_sites_tab():
    sites = load_sites()
    s = _stats(sites)

    cols = st.columns(4)
    cols[0].metric("Всего", s["total"])
    cols[1].metric("Включено", s["enabled"])
    cols[2].metric("Отключено", s["disabled"])
    cols[3].metric("Категорий", len(s["categories"]))

    st.markdown("#### 🔍 Фильтры")
    f1, f2, f3 = st.columns([2, 2, 1])
    with f1:
        search = st.text_input(
            "Поиск по URL или категории",
            placeholder="например: telecom",
        )
    with f2:
        cat_options = ["все"] + sorted(s["categories"].keys())
        cat = st.selectbox("Категория", cat_options, index=0)
    with f3:
        status = st.selectbox("Статус", ["все", "включённые", "отключённые"])

    rows = sites
    if search:
        q = search.lower()
        rows = [
            r
            for r in rows
            if q in r.get("url", "").lower()
            or q in r.get("category", "").lower()
        ]
    if cat != "все":
        rows = [r for r in rows if r.get("category") == cat]
    if status == "включённые":
        rows = [r for r in rows if r.get("enabled")]
    elif status == "отключённые":
        rows = [r for r in rows if not r.get("enabled")]

    st.caption(f"Показано **{len(rows)}** из {len(sites)} сайтов")

    df = pd.DataFrame(rows)
    if df.empty:
        st.info("Нет сайтов под текущие фильтры.")
        return
    for col in ("url", "category", "enabled"):
        if col not in df.columns:
            df[col] = "" if col != "enabled" else False
    df = df[["enabled", "url", "category"]]

    edited = st.data_editor(
        df,
        column_config={
            "enabled": st.column_config.CheckboxColumn(
                "Вкл", default=True, width="small",
            ),
            "url": st.column_config.LinkColumn("URL", width="large"),
            "category": st.column_config.TextColumn("Категория", width="medium"),
        },
        num_rows="dynamic",
        use_container_width=True,
        hide_index=True,
        key="sites_editor",
    )

    a1, a2, a3, a4 = st.columns(4)
    if a1.button("💾 Сохранить", type="primary", use_container_width=True):
        shown_urls = {r["url"] for r in rows}
        edited_by_url: dict[str, dict] = {}
        new_records: list[dict] = []
        for r in edited.to_dict(orient="records"):
            url = (r.get("url") or "").strip()
            if not url:
                continue
            entry = {
                "url": url,
                "category": (r.get("category") or "uncategorized").strip(),
                "enabled": bool(r.get("enabled", True)),
            }
            if url in shown_urls:
                edited_by_url[url] = entry
            else:
                new_records.append(entry)

        out: list[dict] = []
        seen_urls: set[str] = set()
        for s_orig in sites:
            if s_orig["url"] in shown_urls:
                replacement = edited_by_url.get(s_orig["url"])
                if replacement and replacement["url"] not in seen_urls:
                    out.append(replacement)
                    seen_urls.add(replacement["url"])
            elif s_orig["url"] not in seen_urls:
                out.append(s_orig)
                seen_urls.add(s_orig["url"])
        for entry in new_records:
            if entry["url"] not in seen_urls:
                out.append(entry)
                seen_urls.add(entry["url"])

        save_sites(out)
        st.toast(f"Сохранено: {len(out)} записей", icon="💾")
        st.rerun()
    if a2.button("✅ Включить отображённые", use_container_width=True):
        urls_in_view = {r["url"] for r in rows}
        new_list = [
            {**s_, "enabled": True}
            if s_["url"] in urls_in_view
            else s_
            for s_ in sites
        ]
        save_sites(new_list)
        st.toast(f"Включено: {len(urls_in_view)}", icon="✅")
        st.rerun()
    if a3.button("⛔ Отключить отображённые", use_container_width=True):
        urls_in_view = {r["url"] for r in rows}
        new_list = [
            {**s_, "enabled": False}
            if s_["url"] in urls_in_view
            else s_
            for s_ in sites
        ]
        save_sites(new_list)
        st.toast(f"Отключено: {len(urls_in_view)}", icon="⛔")
        st.rerun()
    if a4.button("🗑 Удалить отображённые", use_container_width=True):
        urls_in_view = {r["url"] for r in rows}
        new_list = [s_ for s_ in sites if s_["url"] not in urls_in_view]
        save_sites(new_list)
        st.toast(f"Удалено: {len(urls_in_view)}", icon="🗑")
        st.rerun()

    with st.expander("📊 Распределение по категориям"):
        cat_df = pd.DataFrame(
            [
                {"Категория": k, "Сайтов": v}
                for k, v in sorted(s["categories"].items(), key=lambda x: -x[1])
            ]
        )
        st.dataframe(cat_df, use_container_width=True, hide_index=True)


# ────────────────────────────────────────────────────────────────────
# Tab 3: maintenance


def render_maintenance_tab(settings: RunSettings):
    concurrency = getattr(settings, "concurrency", 3)
    headless = settings.headless

    # ── Verify ────────────────────────────────────────────────────
    st.markdown("#### 🔄 Верификация существующего списка")
    st.markdown(
        '<div class="cb-help">Проверка: HTTP-доступность + наличие callback-формы. '
        'Без формы и мёртвые домены автоматически уезжают в исключения '
        'и больше не перепроверяются при росте.</div>',
        unsafe_allow_html=True,
    )
    if st.button("🔍 Запустить верификацию", type="primary", use_container_width=True):
        sites = load_sites()
        if not sites:
            st.warning("Список пуст.")
        else:
            total = len(sites)
            with st.status(f"Верификация {total} сайтов…", expanded=True) as status:
                progress = st.progress(0.0, text="…")
                log_box = st.empty()
                lines: list[str] = []

                def on_event(event, payload):
                    i, tot, entry, res = payload
                    if res.good:
                        cls, mark = "ok", "✓"
                    elif res.alive:
                        cls, mark = "neutral", "⚠"
                    else:
                        cls, mark = "bad", "✗"
                    line = (
                        f'<span class="{cls}">{mark}</span> '
                        f'<code>{entry["url"]}</code> '
                        f'<span class="neutral">— {res.reason or "OK"}</span>'
                    )
                    lines.append(line)
                    progress.progress(
                        i / max(tot, 1),
                        text=f"{i}/{tot} · {entry['url']}",
                    )
                    log_box.markdown(
                        f'<div class="cb-log">{"<br>".join(lines[-40:])}</div>',
                        unsafe_allow_html=True,
                    )

                async def _factory(emit):
                    def _wrapped(i, tot, entry, res):
                        emit("step", (i, tot, entry, res))
                    return await verify_all(
                        headless=headless,
                        concurrency=concurrency,
                        on_progress=_wrapped,
                        save=True,
                    )

                try:
                    new_list, pairs = _run_async_streaming(_factory, on_event)
                except Exception as e:
                    status.update(label=f"Ошибка: {e!r}", state="error")
                    return

                good = sum(1 for _, r in pairs if r.good)
                alive_no_form = sum(1 for _, r in pairs if r.alive and not r.form_found)
                dead = sum(1 for _, r in pairs if not r.alive)
                status.update(
                    label=(
                        f"Готово: ✓{good}  ⚠{alive_no_form} без формы  "
                        f"✗{dead} мёртвых"
                    ),
                    state="complete",
                )
            st.toast("Список обновлён", icon="🔄")
            st.rerun()

    st.divider()

    # ── Grow ──────────────────────────────────────────────────────
    st.markdown("#### 📈 Расширение списка (поиск новых сайтов)")
    st.markdown(
        '<div class="cb-help">Поиск через DuckDuckGo HTML, при анти-бот блокировке '
        '— автоматический фоллбэк на Bing. Каждый кандидат верифицируется сразу: '
        'в <code>sites.json</code> попадают только домены с реальной формой.</div>',
        unsafe_allow_html=True,
    )
    g1, g2 = st.columns([1, 1])
    target = g1.number_input(
        "Целевое количество включённых сайтов",
        min_value=10, max_value=1000, value=300, step=10,
    )
    if g2.button(
        "🌱 Запустить рост списка",
        type="primary",
        use_container_width=True,
    ):
        with st.status(
            f"Поиск и верификация (цель: {target})…",
            expanded=True,
        ) as status:
            stats_box = st.empty()
            log_box = st.empty()
            lines: list[str] = []
            stats = {
                "good": 0, "bad": 0, "skipped_excluded": 0,
                "current": 0, "target": int(target),
            }

            def render_state():
                stats_box.markdown(_stats_html(stats), unsafe_allow_html=True)
                log_box.markdown(
                    f'<div class="cb-log">{"<br>".join(lines[-50:])}</div>',
                    unsafe_allow_html=True,
                )

            def on_event(event, payload):
                if event == "query_start":
                    stats["current"] = payload["current"]
                    q_idx = payload.get("q_idx", "?")
                    q_total = payload.get("q_total", "?")
                    lines.append(
                        f'<span class="q">[{q_idx}/{q_total}] '
                        f'🔎 {payload["query"]}</span>'
                    )
                elif event == "query_done":
                    src = payload.get("source", "?")
                    if payload["candidates"] == 0:
                        if src == "error":
                            err = payload.get("error", "")
                            lines.append(
                                f'<span class="bad">  ⚠ ошибка поиска: '
                                f'{err}</span>'
                            )
                        else:
                            lines.append(
                                f'<span class="neutral">  ⚪ нет новых '
                                f'кандидатов '
                                f'(пустых раундов: {payload["empty_rounds"]})'
                                f'</span>'
                            )
                    else:
                        lines.append(
                            f'<span class="neutral">  → найдено '
                            f'{payload["candidates"]} ({src}), проверяю…'
                            f'</span>'
                        )
                elif event == "candidate_good":
                    stats["good"] += 1
                    stats["current"] = payload["current"]
                    lines.append(
                        f'<span class="ok">  ✓ {payload["domain"]}</span> '
                        f'<span class="neutral">— добавлен</span>'
                    )
                elif event == "candidate_bad":
                    stats["bad"] += 1
                    lines.append(
                        f'<span class="bad">  ✗ {payload["domain"]}</span> '
                        f'<span class="neutral">— {payload["reason"]}</span>'
                    )
                elif event == "finished":
                    stats["skipped_excluded"] = payload.get(
                        "skipped_excluded", 0,
                    )
                    lines.append(
                        f'<br><b>Итог:</b> '
                        f'<span class="ok">+{payload["new_good"]}</span>, '
                        f'отсеяно <span class="bad">{payload["skipped"]}</span>, '
                        f'пропущено как уже исключённые '
                        f'<span class="neutral">'
                        f'{payload.get("skipped_excluded", 0)}</span>, '
                        f'в файле {payload["total"]}'
                    )
                render_state()

            render_state()

            async def _factory(emit):
                def _wrapped(event, payload):
                    emit(event, payload)
                return await grow(
                    target=int(target),
                    headless=headless,
                    concurrency=concurrency,
                    on_progress=_wrapped,
                )

            try:
                summary = _run_async_streaming(_factory, on_event)
            except Exception as e:
                status.update(label=f"Ошибка: {e!r}", state="error")
                return
            status.update(
                label=(
                    f"Готово: +{summary['new_good']} живых · "
                    f"отсеяно {summary['skipped']} · "
                    f"пропущено {summary.get('skipped_excluded', 0)}"
                ),
                state="complete",
            )
        st.toast("Список пополнен", icon="🌱")
        st.rerun()

    st.divider()

    # ── Quick add ─────────────────────────────────────────────────
    st.markdown("#### ➕ Быстрое добавление сайта")
    st.markdown(
        '<div class="cb-help">Добавит запись только если домен жив и форма '
        'найдена. Полезно для ручной проверки одного сайта.</div>',
        unsafe_allow_html=True,
    )
    a1, a2, a3 = st.columns([3, 2, 1])
    with a1:
        new_url = st.text_input(
            "URL нового сайта", placeholder="https://example.by",
            label_visibility="collapsed",
        )
    with a2:
        new_cat = st.text_input(
            "Категория", value="manual",
            label_visibility="collapsed",
        )
    with a3:
        add_clicked = st.button("Добавить", type="primary", use_container_width=True)

    if add_clicked:
        if not new_url:
            st.warning("Введи URL.")
        else:
            url = new_url.strip()
            if not url.startswith(("http://", "https://")):
                url = "https://" + url
            sites = load_sites()
            if any(s["url"].rstrip("/") == url.rstrip("/") for s in sites):
                st.info("Этот URL уже есть в списке.")
            else:
                with st.spinner(f"Проверяю {url}…"):
                    async def _factory():
                        from tools.verifier import verify_url as vu
                        import httpx as _httpx
                        from src.browser import browser_context as _ctx
                        async with _ctx(headless=headless) as ctx:
                            async with _httpx.AsyncClient(
                                follow_redirects=True,
                            ) as client:
                                return await vu(url, ctx, client)
                    try:
                        res = _run_async(_factory)
                    except Exception as e:
                        st.error(f"Ошибка проверки: {e!r}")
                        return
                if res.good:
                    sites.append(
                        {"url": url, "category": new_cat or "manual",
                         "enabled": True}
                    )
                    save_sites(sites)
                    st.toast(f"✓ Добавлен: {url}", icon="🌱")
                    st.rerun()
                else:
                    reason = res.reason or (
                        "домен мёртв" if not res.alive else "форма не найдена"
                    )
                    st.error(f"Не добавлено: {reason}")

    st.divider()
    render_exclusions_block()


# ────────────────────────────────────────────────────────────────────
# Exclusions panel


def render_exclusions_block():
    st.markdown("#### 🚫 Список исключений")
    st.markdown(
        '<div class="cb-help">Домены, у которых не нашлась callback-форма '
        'или они мёртвы. При росте списка эти домены пропускаются — '
        'это экономит время.</div>',
        unsafe_allow_html=True,
    )

    excluded = load_exclusions()
    st.metric("Записей", len(excluded))

    if not excluded:
        st.info(
            "Список пуст. Запустите верификацию или рост — "
            "сюда автоматически попадут отсеянные домены."
        )
        return

    rows = [
        {"Домен": d, "Причина": v.get("reason", ""), "Когда": v.get("ts", "")}
        for d, v in sorted(excluded.items())
    ]
    df_excl = pd.DataFrame(rows)

    f1, f2 = st.columns([3, 1])
    with f1:
        q = st.text_input(
            "Фильтр по домену или причине",
            placeholder="например: timeout",
            key="excl_filter",
            label_visibility="collapsed",
        )
    with f2:
        st.caption(f"Всего: **{len(rows)}**")

    if q:
        ql = q.lower()
        df_excl = df_excl[df_excl.apply(
            lambda r: ql in str(r["Домен"]).lower()
            or ql in str(r["Причина"]).lower(),
            axis=1,
        )]

    st.dataframe(df_excl, use_container_width=True, hide_index=True, height=260)

    e1, e2, e3 = st.columns([2, 2, 2])
    with e1:
        unblock = st.text_input(
            "Разблокировать домен", placeholder="example.by",
            key="excl_unblock", label_visibility="collapsed",
        )
    with e2:
        if st.button(
            "↩️ Убрать из исключений", use_container_width=True,
            disabled=not bool(unblock),
        ):
            if remove_exclusion(unblock.strip().lower()):
                st.toast(f"Домен {unblock} разблокирован", icon="↩️")
                st.rerun()
            else:
                st.warning("Такого домена нет в списке.")
    with e3:
        if st.button(
            "🗑 Очистить весь список",
            use_container_width=True,
            help=(
                "Все исключения будут удалены — при следующем росте "
                "домены могут вернуться."
            ),
        ):
            n = clear_exclusions()
            st.toast(f"Удалено: {n}", icon="🗑")
            st.rerun()


# ────────────────────────────────────────────────────────────────────
# main


def main():
    st.set_page_config(
        page_title="Callback BY", page_icon="📞", layout="wide",
        initial_sidebar_state="expanded",
    )
    st.markdown(CUSTOM_CSS, unsafe_allow_html=True)

    st.title("📞 Callback BY")
    st.caption(
        "Массовая отправка форм обратного звонка на белорусских сайтах · "
        "Streamlit + Playwright"
    )

    settings, confirmed = render_sidebar()
    render_topbar()
    st.markdown(" ")

    tab_send, tab_sites, tab_maint = st.tabs([
        "🚀 Рассылка",
        "📋 Сайты",
        "🛠 Обслуживание",
    ])

    with tab_send:
        render_send_tab(settings, confirmed)
    with tab_sites:
        render_sites_tab()
    with tab_maint:
        render_maintenance_tab(settings)


if __name__ == "__main__":
    main()
