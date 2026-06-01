from __future__ import annotations

from playwright.async_api import Page, TimeoutError as PWTimeoutError

from .config import load_keywords
from .form_finder import FormHandle
from .phone import variants
from .result import Status

_SUCCESS_HINTS: list[str] = load_keywords()["success_indicators"]


async def _set_input_value(input_locator, value: str) -> bool:
    try:
        await input_locator.evaluate(
            """
            (el, nextValue) => {
                el.focus();
                if (typeof el.select === 'function') {
                    el.select();
                }
                el.value = '';
                el.dispatchEvent(new Event('input', { bubbles: true }));
                el.value = nextValue;
                el.dispatchEvent(new Event('input', { bubbles: true }));
                el.dispatchEvent(new Event('change', { bubbles: true }));
                el.dispatchEvent(new Event('blur', { bubbles: true }));
            }
            """,
            value,
            timeout=2000,
        )
        return True
    except Exception:
        return False


async def _try_fill_phone(handle: FormHandle, phone_raw: str) -> bool:
    for value in variants(phone_raw):
        try:
            await handle.phone_input.click(timeout=2000)
        except Exception:
            pass
        try:
            await handle.phone_input.press("Control+a", timeout=800)
            await handle.phone_input.press("Delete", timeout=800)
        except Exception:
            pass

        for strategy in (
            lambda: handle.phone_input.fill("", timeout=1500),
            lambda: handle.phone_input.type(value, delay=30),
        ):
            try:
                await strategy()
            except Exception:
                pass
        try:
            current = await handle.phone_input.input_value(timeout=1500)
        except Exception:
            current = ""
        digits_in = "".join(c for c in current if c.isdigit())
        if len(digits_in) >= 9:
            return True

        if await _set_input_value(handle.phone_input, value):
            try:
                current = await handle.phone_input.input_value(timeout=1500)
            except Exception:
                current = ""
            digits_in = "".join(c for c in current if c.isdigit())
            if len(digits_in) >= 9:
                return True

        digits_only = "".join(c for c in value if c.isdigit())
        if digits_only and await _set_input_value(handle.phone_input, digits_only):
            try:
                current = await handle.phone_input.input_value(timeout=1500)
            except Exception:
                current = ""
            digits_in = "".join(c for c in current if c.isdigit())
            if len(digits_in) >= 9:
                return True
    return False


async def _try_fill_message(handle: FormHandle, message: str) -> bool:
    if not message or handle.message_input is None:
        return False
    try:
        await handle.message_input.fill(message, timeout=2000)
        return True
    except Exception:
        return False


async def _detect_success(page: Page) -> bool:
    try:
        await page.wait_for_load_state("networkidle", timeout=4000)
    except PWTimeoutError:
        pass
    try:
        body = await page.locator("body").inner_text(timeout=2000)
    except Exception:
        return False
    body_lower = body.lower()
    return any(hint in body_lower for hint in _SUCCESS_HINTS)


async def submit_form(
    page: Page, handle: FormHandle, phone: str, message: str
) -> tuple[Status, bool, bool, str]:
    """Заполнить и отправить форму.
    
    Returns (status, phone_sent, message_sent, error).
    """
    phone_ok = await _try_fill_phone(handle, phone)
    if not phone_ok:
        return Status.FAILED, False, False, "не удалось ввести телефон в поле"

    msg_ok = await _try_fill_message(handle, message)

    try:
        await handle.submit_button.evaluate("el => el.click()", timeout=2000)
    except Exception:
        try:
            await handle.submit_button.click(timeout=4000, force=True, no_wait_after=True)
        except Exception as e:
            return Status.FAILED, True, msg_ok, f"submit click: {e!r}"

    if await _detect_success(page):
        return Status.SUCCESS, True, msg_ok, ""
    return Status.SUBMITTED, True, msg_ok, ""
