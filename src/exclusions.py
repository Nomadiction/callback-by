"""Чёрный список доменов, у которых верификация не нашла callback-форму или они мертвы.

Хранится в data/excluded_domains.json. Используется в `grow`, чтобы не перепроверять
уже отсеянные домены повторно при росте списка.
"""
from __future__ import annotations

import json
from datetime import datetime

from src.config import DATA_DIR

EXCLUSIONS_FILE = DATA_DIR / "excluded_domains.json"


def load_exclusions() -> dict[str, dict]:
    if not EXCLUSIONS_FILE.exists():
        return {}
    try:
        return json.loads(EXCLUSIONS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_exclusions(d: dict[str, dict]) -> None:
    EXCLUSIONS_FILE.write_text(
        json.dumps(d, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def add_many(items: list[tuple[str, str]]) -> None:
    if not items:
        return
    d = load_exclusions()
    now = datetime.now().isoformat(timespec="seconds")
    for domain, reason in items:
        if not domain:
            continue
        d[domain] = {"reason": reason, "ts": now}
    save_exclusions(d)


def add_many_with_ts(items: list[tuple[str, str, str | None]]) -> None:
    if not items:
        return
    d = load_exclusions()
    now = datetime.now().isoformat(timespec="seconds")
    for domain, reason, ts in items:
        if not domain:
            continue
        d[domain] = {"reason": reason, "ts": ts or now}
    save_exclusions(d)


def remove(domain: str) -> bool:
    d = load_exclusions()
    if domain in d:
        del d[domain]
        save_exclusions(d)
        return True
    return False


def clear() -> int:
    n = len(load_exclusions())
    save_exclusions({})
    return n


def excluded_domains() -> set[str]:
    return set(load_exclusions().keys())
