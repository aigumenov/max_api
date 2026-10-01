"""
Long-polling loop для бота в MAX messenger.

Реализует пошаговый сценарий:
  1. Приветствие + кнопка "СТАРТ".
  2. Правила + чекбоксы (два документа).
  3. Проверка, что оба чекбокса отмечены -> "Отлично! Мы начинаем."
  4. Пояснение + запрос места рождения (с пояснением про индекс).
  5. Геокодирование и вывод координат/часового пояса.
"""

import os
import asyncio
import logging
from typing import Callable

import httpx

from dialog import (
    Step,
    WELCOME_TEXT,
    START_BUTTON_TEXT,
    CONSENT_TEXT,
    CONSENT_ITEMS,
    CONSENT_OK_TEXT,
    BIRTH_PLACE_INTRO_TEXT,
    BIRTH_PLACE_ASK_TEXT,
    BIRTH_PLACE_RETRY_TEXT,
    ERROR_TEXT,
)

logger = logging.getLogger("max-interaction")

MAX_API_TOKEN: str | None = os.getenv("MAX_API_TOKEN")
MAX_API_BASE_URL: str = os.getenv("MAX_API_BASE_URL", "https://platform-api2.max.ru")

POLL_TIMEOUT = 30
HTTP_TIMEOUT = POLL_TIMEOUT + 10
RETRY_DELAY = 3


# ---------------------------------------------------------------------------
# Состояния пользователей
# ---------------------------------------------------------------------------
_user_state: dict[int, dict] = {}


def _get_state(user_id: int) -> dict:
    st = _user_state.get(user_id)
    if st is None:
        st = {"step": Step.NEW, "consent": {item["id"]: False for item in CONSENT_ITEMS}}
        _user_state[user_id] = st
    return st


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------
def _headers() -> dict:
    return {"Authorization": MAX_API_TOKEN}


async def _send_message(
    client: httpx.AsyncClient,
    user_id: int,
    text: str,
    attachments: list | None = None,
) -> None:
    url = f"{MAX_API_BASE_URL.rstrip('/')}/messages"
    params = {"user_id": user_id}
    payload: dict = {"text": text}
    if attachments:
        payload["attachments"] = attachments
    try:
        r = await client.post(url, params=params, json=payload, headers=_headers())
        if r.status_code >= 400:
            logger.warning("send_message failed: HTTP %s | %s", r.status_code, r.text)
    except httpx.RequestError as exc:
        logger.warning("send_message network error: %s", exc)


# ---------------------------------------------------------------------------
# Клавиатуры / вложения
# ---------------------------------------------------------------------------
def _kb_start() -> list:
    """Кнопка «СТАРТ» (inline callback)."""
    return [
        {
            "type": "inline_keyboard",
            "payload": {
                "buttons": [
                    [
                        {
                            "type": "callback",
                            "text": START_BUTTON_TEXT,
                            "payload": "start",
                        }
                    ]
                ]
            },
        }
    ]


def _kb_consent(selected: dict[str, bool]) -> list:
    """Два чекбокса для принятия правил."""
    return [
        {
            "type": "inline_keyboard",
            "payload": {
                "buttons": [
                    [
                        {
                            "type": "callback",
                            "text": ("✅ " if selected.get(item["id"]) else "⬜ ") + item["label"],
                            "payload": f"consent:{item['id']}",
                        }
                    ]
                    for item in CONSENT_ITEMS
                ]
            },
        }
    ]


# ---------------------------------------------------------------------------
# Polling
# ---------------------------------------------------------------------------
async def _get_updates(
    client: httpx.AsyncClient, marker: int | None
) -> tuple[list[dict], int | None]:
    url = f"{MAX_API_BASE_URL.rstrip('/')}/updates"
    params: dict = {"timeout": POLL_TIMEOUT, "limit": 100}
    if marker is not None:
        params["marker"] = marker

    r = await client.get(url, params=params, headers=_headers())
    r.raise_for_status()
    data = r.json()
    return data.get("updates", []) or [], data.get("marker")


def _format_location(info: dict) -> str:
    return (
        f"📍 {info['name']}\n"
        f"🕒 Часовой пояс: {info['timezone']}\n"
        f"Широта: {info['latitude']:.6f}\n"
        f"Долгота: {info['longitude']:.6f}"
    )


# ---------------------------------------------------------------------------
# Шаги диалога
# ---------------------------------------------------------------------------
async def _step_new(client, user_id: int, text: str, state: dict) -> None:
    await _send_message(client, user_id, WELCOME_TEXT, attachments=_kb_start())
    state["step"] = Step.AWAIT_START


async def _step_await_start(client, user_id: int, text: str, state: dict) -> None:
    if text.strip().lower() in ("start", "/start", "старт"):
        state["consent"] = {item["id"]: False for item in CONSENT_ITEMS}
        await _send_message(
            client,
            user_id,
            CONSENT_TEXT,
            attachments=_kb_consent(state["consent"]),
        )
        state["step"] = Step.AWAIT_CONSENT
    else:
        await _send_message(
            client,
            user_id,
            "Пожалуйста, нажмите «СТАРТ», чтобы продолжить.",
            attachments=_kb_start(),
        )


async def _step_await_consent(client, user_id: int, text: str, state: dict) -> None:
    low = text.strip().lower()

    if low in ("privacy", "pd"):
        state["consent"][low] = not state["consent"].get(low, False)

    if low in ("принимаю", "согласен", "согласна", "ок", "ok", "да"):
        for item in CONSENT_ITEMS:
            state["consent"][item["id"]] = True

    if all(state["consent"].values()):
        await _send_message(client, user_id, CONSENT_OK_TEXT)
        await _send_message(client, user_id, BIRTH_PLACE_INTRO_TEXT)
        await _send_message(client, user_id, BIRTH_PLACE_ASK_TEXT)
        state["step"] = Step.AWAIT_BIRTH_PLACE
        return

    await _send_message(
        client,
        user_id,
        "Пожалуйста, отметьте оба пункта, чтобы продолжить.",
        attachments=_kb_consent(state["consent"]),
    )


async def _step_await_birth_place(
    client,
    user_id: int,
    text: str,
    state: dict,
    geocode_func: Callable[[str], dict],
) -> None:
    query = text.strip()
    if not query:
        await _send_message(client, user_id, BIRTH_PLACE_ASK_TEXT)
        return

    try:
        loop = asyncio.get_running_loop()
        info = await loop.run_in_executor(None, geocode_func, query)
    except Exception as exc:  # noqa: BLE001
        logger.exception("geocode error: %s", exc)
        await _send_message(client, user_id, ERROR_TEXT)
        return

    if not info or "error" in info:
        await _send_message(client, user_id, BIRTH_PLACE_RETRY_TEXT)
        return

    await _send_message(client, user_id, _format_location(info))
    state["step"] = Step.DONE


# ---------------------------------------------------------------------------
# Диспетчеры
# ---------------------------------------------------------------------------
async def _handle_message(
    client: httpx.AsyncClient,
    update: dict,
    geocode_func: Callable[[str], dict],
) -> None:
    msg = update.get("message") or {}
    sender = msg.get("sender") or {}
    user_id = sender.get("user_id")
    text = (msg.get("body") or {}).get("text", "").strip()

    if not user_id or sender.get("is_bot"):
        return

    state = _get_state(user_id)
    step = state["step"]

    logger.info("Incoming from %s [%s]: %s", user_id, step, text)

    # Если пользователь на шаге NEW — показываем приветствие,
    # а его текст (если это не «старт») обрабатываем на следующем шаге.
    if step == Step.NEW:
        await _step_new(client, user_id, text, state)
        # если сразу написал «старт» — обработаем
        if text.strip().lower() in ("start", "/start", "старт"):
            await _step_await_start(client, user_id, text, state)
        return

    if step == Step.AWAIT_START:
        await _step_await_start(client, user_id, text, state)
    elif step == Step.AWAIT_CONSENT:
        await _step_await_consent(client, user_id, text, state)
    elif step == Step.AWAIT_BIRTH_PLACE:
        await _step_await_birth_place(client, user_id, text, state, geocode_func)
    else:
        state["step"] = Step.AWAIT_BIRTH_PLACE
        await _send_message(client, user_id, BIRTH_PLACE_ASK_TEXT)


async def _handle_callback(
    client: httpx.AsyncClient,
    update: dict,
    geocode_func: Callable[[str], dict],
) -> None:
    """Обработка нажатий на inline-кнопки (callback)."""
    cb = update.get("callback") or {}
    payload = (cb.get("payload") or "").strip()
    user = cb.get("user") or update.get("user") or {}
    user_id = user.get("user_id") or user.get("id")

    if not user_id or not payload:
        logger.warning("callback without user_id/payload: %s", update)
        return

    state = _get_state(user_id)
    logger.info("Callback from %s [%s]: %s", user_id, state["step"], payload)

    if payload == "start":
        await _step_await_start(client, user_id, "старт", state)
        return

    if payload.startswith("consent:"):
        key = payload.split(":", 1)[1]
        if key in state["consent"]:
            state["consent"][key] = not state["consent"][key]
        await _step_await_consent(client, user_id, "", state)
        return

    logger.warning("Unknown callback payload: %s", payload)


# ---------------------------------------------------------------------------
# Точка входа polling-цикла
# ---------------------------------------------------------------------------
async def run_interaction(geocode_func: Callable[[str], dict]) -> None:
    if not MAX_API_TOKEN:
        logger.error("MAX_API_TOKEN is not set — polling not started")
        return

    marker: int | None = None
    logger.info("Astro-bot polling started.")

    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        while True:
            try:
                updates, marker = await _get_updates(client, marker)
                for upd in updates:
                    utype = upd.get("update_type")
                    if utype == "message_created":
                        await _handle_message(client, upd, geocode_func)
                    elif utype == "message_callback":
                        await _handle_callback(client, upd, geocode_func)
            except asyncio.CancelledError:
                logger.info("Polling cancelled")
                raise
            except httpx.HTTPStatusError as exc:
                logger.warning(
                    "Polling HTTP error: %s | %s",
                    exc.response.status_code,
                    exc.response.text,
                )
                await asyncio.sleep(RETRY_DELAY)
            except httpx.RequestError as exc:
                logger.warning("Polling network error: %s", exc)
                await asyncio.sleep(RETRY_DELAY)
            except Exception as exc:  # noqa: BLE001
                logger.exception("Polling unexpected error: %s", exc)
                await asyncio.sleep(RETRY_DELAY)