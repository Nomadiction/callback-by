from __future__ import annotations

import csv
from dataclasses import dataclass, asdict, field
from datetime import datetime
from enum import Enum
from pathlib import Path


class Status(str, Enum):
    SUCCESS = "успех"
    SUBMITTED = "отправлено (без подтверждения)"
    NO_FORM = "форма не найдена"
    FAILED = "ошибка"
    TIMEOUT = "таймаут"
    UNREACHABLE = "сайт недоступен"


@dataclass
class SiteResult:
    site: str
    form_found: bool = False
    phone_sent: bool = False
    message_sent: bool = False
    status: Status = Status.FAILED
    error: str = ""
    timestamp: str = field(
        default_factory=lambda: datetime.now().isoformat(timespec="seconds")
    )

    def as_row(self) -> dict:
        d = asdict(self)
        d["status"] = self.status.value
        return d


def write_csv(results: list[SiteResult], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not results:
        return
    rows = [r.as_row() for r in results]
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
