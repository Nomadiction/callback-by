from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
LOGS_DIR = ROOT / "logs"
SITES_FILE = DATA_DIR / "sites.json"
KEYWORDS_FILE = DATA_DIR / "keywords.json"

LOGS_DIR.mkdir(exist_ok=True)


@dataclass
class RunSettings:
    phone: str
    message: str = ""
    headless: bool = True
    pause_min: float = 2.0
    pause_max: float = 5.0
    max_sites: int = 20
    discover_new: bool = False
    per_site_timeout_ms: int = 25_000  # Оптимизирован до 25сек для больших прогонов
    probe_timeout_sec: float = 8.0  # HTTP проба
    form_find_timeout_sec: float = 15.0  # Поиск формы
    max_daily_submissions: int = 200  # Защита от DDoS


def load_keywords() -> dict:
    return json.loads(KEYWORDS_FILE.read_text(encoding="utf-8"))


def load_sites() -> list[dict]:
    return json.loads(SITES_FILE.read_text(encoding="utf-8"))


def save_sites(sites: list[dict]) -> None:
    SITES_FILE.write_text(
        json.dumps(sites, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
