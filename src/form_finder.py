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

CAPTCHA_SELECTORS = [
    'iframe[src*="recaptcha"]',
    'iframe[src*="hcaptcha"]',
    'iframe[src*="turnstile"]',
    'iframe[src*="captcha"]',
    'div.g-recaptcha',
    'div.h-captcha',
    'div.cf-turnstile',
    'div[class*="captcha" i]',
    'script[src*="recaptcha"]',
    'script[src*="hcaptcha"]',
    'script[src*="turnstile"]',
]

TRIGGER_CLASS_HINTS = [
    "callback", "call-back", "call_back",
    "appointment", "booking", "book-now",
    "consult", "feedback", "request",
    "order-call", "order_call", "ordercall",
    "phone-order", "phone_request",
    "make-appointment", "online-zapis", "zapis",
]


@dataclass
class FormHandle:
    phone_input: Locator
    message_input: Optional[Locator]
    submit_button: Locator
    container: Locator


async def has_captcha(page: Page) -> bool:
    for sel in CAPTCHA_SELECTORS:
        try:
            loc = page.locator(sel)
            if await loc.count() > 0:
                first = loc.first
                try:
                    if await first.is_visible():
                        return True
                except Exception:
                    return True
        except Exception:
            continue
    return False


def _phone_selector() -> str:
    parts = ['input[type="tel"]']
    for h in _PHONE_HINTS:
        parts += [
            f'input[name*="{h}" i]',
            f'input[id*="{h}" i]',
            f'input[placeholder*="{h}" i]',
            f'input[autocomplete*="{h}" i]',
            f'input[aria-label*="{h}" i]',
        ]
    return ", ".join(parts)


def _message_selector() -> str:
    parts = ["textarea"]
    for h in _MSG_HINTS:
        parts += [
            f'input[name*="{h}" i]',
            f'textarea[name*="{h}" i]',
            f'textarea[placeholder*="{h}" i]',
            f'textarea[aria-label*="{h}" i]',
        ]
    return ", ".join(parts)


def _trigger_selector(trigger_text: str) -> str:
    return ", ".join([
        f'button:has-text("{trigger_text}")',
        f'a:has-text("{trigger_text}")',
        f'[role="button"]:has-text("{trigger_text}")',
        f'button[aria-label*="{trigger_text}" i]',
        f'a[aria-label*="{trigger_text}" i]',
        f'[title*="{trigger_text}" i]',
    ])


def _class_trigger_selector() -> str:
    parts: list[str] = []
    for cls in TRIGGER_CLASS_HINTS:
        parts += [
            f'button[class*="{cls}" i]',
            f'a[class*="{cls}" i]',
            f'[role="button"][class*="{cls}" i]',
            f'div[class*="{cls}" i]',
        ]
    return ", ".join(parts)


async def _find_visible_phone_input(scope: Page | Locator) -> Optional[Locator]:
    loc = scope.locator(_phone_selector())
    count = await loc.count()
    for i in range(min(count, 15)):
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
        loc = container.locator(
            f'button:has-text("{hint}"), '
            f'a:has-text("{hint}"), '
            f'[role="button"]:has-text("{hint}")'
        )
        count = await loc.count()
        for i in range(min(count, 5)):
            cand = loc.nth(i)
            try:
                if await cand.is_visible():
                    return cand
            except Exception:
                continue

    generic = container.locator(
        'button:not([type="reset"]):not([type="button"]), '
        'button[type="button"]'
    )
    g_count = await generic.count()
    for i in range(min(g_count, 8)):
        cand = generic.nth(i)
        try:
            if not await cand.is_visible():
                continue
            text = (await cand.inner_text(timeout=500)).strip().lower()
            if any(h in text for h in _SUBMIT_HINTS):
                return cand
        except Exception:
            continue
    return None


async def _autofill_required_selects(container: Locator) -> None:
    """Modal forms (medavenu/nordin) often have required <select> fields like
    'выбрать врача / любой врач'. Pick the first non-empty option so the
    submit doesn't bounce on validation.
    """
    try:
        selects = container.locator("select")
        s_count = await selects.count()
    except Exception:
        s_count = 0
    for i in range(min(s_count, 5)):
        sel = selects.nth(i)
        try:
            if not await sel.is_visible(timeout=500):
                continue
            current = await sel.input_value(timeout=500)
            if current:
                continue
            options = sel.locator("option")
            opt_count = await options.count()
            for j in range(opt_count):
                val = await options.nth(j).get_attribute("value", timeout=500)
                if val:
                    await sel.select_option(value=val, timeout=1500)
                    break
        except Exception:
            continue

    try:
        consents = container.locator(
            'input[type="checkbox"][required], '
            'input[type="checkbox"][name*="consent" i], '
            'input[type="checkbox"][name*="agree" i], '
            'input[type="checkbox"][name*="согла" i]'
        )
        c_count = await consents.count()
    except Exception:
        c_count = 0
    for i in range(min(c_count, 5)):
        cb = consents.nth(i)
        try:
            if not await cb.is_visible(timeout=500):
                continue
            if not await cb.is_checked(timeout=500):
                try:
                    await cb.evaluate(
                        "el => { el.checked = true; "
                        "el.dispatchEvent(new Event('change', {bubbles:true})); }",
                        timeout=1000,
                    )
                except Exception:
                    await cb.check(timeout=1500, force=True)
        except Exception:
            continue


async def _build_handle(phone_input: Locator) -> Optional[FormHandle]:
    container = phone_input.locator("xpath=ancestor::form[1]")
    if await container.count() == 0:
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


async def _safe_click(btn: Locator) -> bool:
    """Try DOM click first (bypasses overlay/iframe pointer interception),
    then real click as fallback. Each step bounded by short timeout.
    """
    try:
        await btn.evaluate("el => el.click()", timeout=2000)
        return True
    except Exception:
        pass
    try:
        await btn.click(timeout=2000, force=True, no_wait_after=True)
        return True
    except Exception:
        return False


async def _try_trigger(page: Page, btn: Locator) -> Optional[FormHandle]:
    try:
        visible = await btn.is_visible(timeout=800)
    except Exception:
        return None
    if not visible:
        return None
    if not await _safe_click(btn):
        return None
    try:
        await page.wait_for_selector(
            _phone_selector(),
            state="visible",
            timeout=4000,
        )
    except PWTimeoutError:
        return None
    except Exception:
        return None
    phone_input = await _find_visible_phone_input(page)
    if phone_input is None:
        return None
    handle = await _build_handle(phone_input)
    if handle is not None:
        await _autofill_required_selects(handle.container)
    return handle


async def find_form(page: Page, *, budget_sec: float = 15.0) -> Optional[FormHandle]:
    """Direct phone field → text triggers → class/aria triggers.

    Жёсткий бюджет времени. На капче — сразу None.
    """
    import asyncio as _asyncio
    import time as _time

    deadline = _time.monotonic() + budget_sec

    def _left() -> float:
        return max(0.0, deadline - _time.monotonic())

    if await has_captcha(page):
        return None

    direct = await _find_visible_phone_input(page)
    if direct is not None:
        handle = await _build_handle(direct)
        if handle is not None:
            await _autofill_required_selects(handle.container)
            return handle

    for trigger in _TRIGGERS:
        if _left() < 1.0:
            return None
        try:
            triggers = page.locator(_trigger_selector(trigger))
            count = await triggers.count()
        except Exception:
            continue
        for i in range(min(count, 2)):
            if _left() < 1.0:
                return None
            try:
                handle = await _asyncio.wait_for(
                    _try_trigger(page, triggers.nth(i)),
                    timeout=min(_left(), 6.0),
                )
            except _asyncio.TimeoutError:
                continue
            except Exception:
                continue
            if handle is not None:
                if await has_captcha(page):
                    return None
                return handle

    if _left() < 2.0:
        return None
    try:
        class_triggers = page.locator(_class_trigger_selector())
        c_count = await class_triggers.count()
    except Exception:
        c_count = 0
    for i in range(min(c_count, 3)):
        if _left() < 1.0:
            return None
        try:
            handle = await _asyncio.wait_for(
                _try_trigger(page, class_triggers.nth(i)),
                timeout=min(_left(), 5.0),
            )
        except _asyncio.TimeoutError:
            continue
        except Exception:
            continue
        if handle is not None:
            if await has_captcha(page):
                return None
            return handle

    return None
