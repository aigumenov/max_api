"""
Long-polling loop for MAX messenger bot.

- При первом сообщении от пользователя отправляет приветствие.
- На последующие — пытается геокодировать название населённого пункта.
- Мусорные/приветственные сообщения отсекаются до вызова geopy.
"""

import os
import re
import asyncio
import logging
from typing import Callable

import httpx

logger = logging.getLogger("max-interaction")

MAX_API_TOKEN: str | None = os.getenv("MAX_API_TOKEN")
MAX_API_BASE_URL: str = os.getenv("MAX_API_BASE_URL", "https://platform-api2.max.ru")

POLL_TIMEOUT = 30
HTTP_TIMEOUT = POLL_TIMEOUT + 10
RETRY_DELAY = 3

WELCOME_TEXT = (
    "Привет! 👋\n"
    "Отправь мне название населённого пункта, "
    "и я пришлю его координаты и часовой пояс."
)
ASK_CITY_TEXT = "Пожалуйста, отправьте название населённого пункта (например: Казань)."
NOT_FOUND_TEXT = "населённый пункт не найден"
ERROR_TEXT = "Не удалось получить данные о населённом пункте. Попробуйте позже."

# Слова, которые не являются названиями городов
_GREETING_WORDS = {
    "hi", "hello", "hey", "start", "/start", "help", "/help",
    "привет", "здравствуйте", "здравствуй", "хай", "ку",
    "добрый день", "добрый вечер", "доброе утро",
}

# Кому уже отправляли приветствие
_greeted: set[int] = set()


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------
def _headers() -> dict:
    return {"Authorization": MAX_API_TOKEN}


async def _send_message(client: httpx.AsyncClient, user_id: int, text: str) -> None:
    url = f"{MAX_API_BASE_URL.rstrip('/')}/messages"
    params = {"user_id": user_id}
    payload = {"text": text}
    try:
        r = await client.post(url, params=params, json=payload, headers=_headers())
        if r.status_code >= 400:
            logger.warning("send_message failed: HTTP %s | %s", r.status_code, r.text)
    except httpx.RequestError as exc:
        logger.warning("send_message network error: %s", exc)


# ---------------------------------------------------------------------------
# Input validation
# ---------------------------------------------------------------------------
_LETTER_RE = re.compile(r"[A-Za-zА-Яа-яЁё]")


def _looks_like_city(text: str) -> bool:
    """Грубая эвристика: похоже ли сообщение на название населённого пункта."""
    t = text.strip()
    if len(t) < 2 or len(t) > 100:
        return False
    if t.lower() in _GREETING_WORDS:
        return False
    # должна быть хотя бы одна буква
    if not _LETTER_RE.search(t):
        return False
    # не должно быть явных URL/email
    if "://" in t or "@" in t:
        return False
    return True


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


async def _handle_message(
    client: httpx.AsyncClient,
    update: dict,
    geocode_func: Callable[[str], dict],
) -> None:
    msg = update.get("message") or {}
    sender = msg.get("sender") or {}
    user_id = sender.get("user_id")
    text = (msg.get("body") or {}).get("text", "").strip()

    if not user_id or not text:
        return
    if sender.get("is_bot"):
        return

    logger.info("Incoming from %s: %s", user_id, text)

    # --- первое сообщение от пользователя: приветствие -------------------
    if user_id not in _greeted:
        _greeted.add(user_id)
        await _send_message(client, user_id, WELCOME_TEXT)
        # если пользователь сразу прислал название — обработаем ниже,
        # иначе просто выходим
        if not _looks_like_city(text):
            return

    # --- валидация -------------------------------------------------------
    if not _looks_like_city(text):
        await _send_message(client, user_id, ASK_CITY_TEXT)
        return

    # --- геокодинг -------------------------------------------------------
    try:
        loop = asyncio.get_running_loop()
        info = await loop.run_in_executor(None, geocode_func, text)
    except Exception as exc:  # noqa: BLE001
        logger.exception("geocode error: %s", exc)
        await _send_message(client, user_id, ERROR_TEXT)
        return

    if not info or "error" in info:
        await _send_message(client, user_id, NOT_FOUND_TEXT)
        return

    await _send_message(client, user_id, _format_location(info))


async def run_interaction(geocode_func: Callable[[str], dict]) -> None:
    if not MAX_API_TOKEN:
        logger.error("MAX_API_TOKEN is not set — polling not started")
        return

    marker: int | None = None
    logger.info("Welcome text: %s", WELCOME_TEXT)

    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        while True:
            try:
                updates, marker = await _get_updates(client, marker)
                for upd in updates:
                    if upd.get("update_type") == "message_created":
                        await _handle_message(client, upd, geocode_func)
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