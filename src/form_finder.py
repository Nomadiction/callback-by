from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from playwright.async_api import Locator, Page, TimeoutError as PWTimeoutError

from .config import load_keywords

_KW = load_keywords()
_TRIGGERS: list[str] = _KW["form_triggers"]
_PHONE_HINTS: list[str] = _KW["phone_field_hints"]
_MSG_HINTS: list[str] = _KW["message_field_hints"]
_SUBMIT_HINTS: list[str] = _KW["submit_button_hints"]


@dataclass
class FormHandle:
    phone_input: Locator
    message_input: Optional[Locator]
    submit_button: Locator
    container: Locator


def _phone_selector() -> str:
    parts = ['input[type="tel"]']
    for h in _PHONE_HINTS:
        parts += [
            f'input[name*="{h}" i]',
            f'input[id*="{h}" i]',
            f'input[placeholder*="{h}" i]',
            f'input[autocomplete*="{h}" i]',
        ]
    return ", ".join(parts)


def _message_selector() -> str:
    parts = ["textarea"]
    for h in _MSG_HINTS:
        parts += [
            f'input[name*="{h}" i]',
            f'textarea[name*="{h}" i]',
            f'textarea[placeholder*="{h}" i]',
        ]
    return ", ".join(parts)


async def _find_visible_phone_input(scope: Page | Locator) -> Optional[Locator]:
    loc = scope.locator(_phone_selector())
    count = await loc.count()
    for i in range(min(count, 10)):
        candidate = loc.nth(i)
        try:
            if await candidate.is_visible():
                return candidate
        except Exception:
            continue
    return None


async def _find_submit(container: Locator) -> Optional[Locator]:
    explicit = container.locator('button[type="submit"], input[type="submit"]')
    if await explicit.count() > 0:
        first = explicit.first
        try:
            if await first.is_visible():
                return first
        except Exception:
            pass

    for hint in _SUBMIT_HINTS:
        loc = container.locator(f'button:has-text("{hint}"), a:has-text("{hint}")')
        count = await loc.count()
        for i in range(min(count, 5)):
            cand = loc.nth(i)
            try:
                if await cand.is_visible():
                    return cand
            except Exception:
                continue
    return None


async def _build_handle(phone_input: Locator) -> Optional[FormHandle]:
    container = phone_input.locator("xpath=ancestor::form[1]")
    if await container.count() == 0:
        # fallback: ближайший контейнер модалки
        container = phone_input.locator(
            "xpath=ancestor::*[self::div or self::section][.//button "
            "or .//input[@type='submit']][1]"
        )
        if await container.count() == 0:
            return None

    submit = await _find_submit(container)
    if submit is None:
        return None

    msg_loc = container.locator(_message_selector())
    msg = msg_loc.first if await msg_loc.count() > 0 else None
    if msg is not None:
        try:
            if not await msg.is_visible():
                msg = None
        except Exception:
            msg = None

    return FormHandle(
        phone_input=phone_input,
        message_input=msg,
        submit_button=submit,
        container=container,
    )


async def find_form(page: Page) -> Optional[FormHandle]:
    """Двухступенчатая стратегия: прямая форма → клик по триггеру модалки → повтор.
    
    Таймаут оптимизирован для production.
    """
    direct = await _find_visible_phone_input(page)
    if direct is not None:
        handle = await _build_handle(direct)
        if handle is not None:
            return handle

    for trigger in _TRIGGERS:
        sel = (
            f'button:has-text("{trigger}"), '
            f'a:has-text("{trigger}"), '
            f'[class*="callback" i]:has-text("{trigger}")'
        )
        triggers = page.locator(sel)
        count = await triggers.count()
        for i in range(min(count, 5)):
            btn = triggers.nth(i)
            try:
                if not await btn.is_visible():
                    continue
                await btn.click(timeout=3000)
            except (PWTimeoutError, Exception):
                continue
            try:
                await page.wait_for_selector(
                    _phone_selector(),
                    state="visible",
                    timeout=5000,
                )
            except PWTimeoutError:
                continue
            phone_input = await _find_visible_phone_input(page)
            if phone_input is None:
                continue
            handle = await _build_handle(phone_input)
            if handle is not None:
                return handle

    return None
