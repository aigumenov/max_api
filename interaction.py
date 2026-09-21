"""
interaction.py — bot polling loop for MAX messenger.

Replies to any incoming text message with a simple "not ready" response.
Imported and started from main.py after the connection check passes.
"""

import asyncio
import logging
import os

from maxapi import Bot, Dispatcher
from maxapi.types import MessageCreated

logger = logging.getLogger("interaction")

# ---------------------------------------------------------------------------
# Bot setup
# ---------------------------------------------------------------------------
MAX_API_TOKEN = os.getenv("MAX_API_TOKEN")

if not MAX_API_TOKEN:
    raise RuntimeError("MAX_API_TOKEN is not set in environment variables")

bot = Bot(MAX_API_TOKEN)
dp = Dispatcher()

NOT_READY_TEXT = "i'm not 100 ready at this time, try later"


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------
@dp.message_created()
async def handle_any_message(event: MessageCreated) -> None:
    """Reply to any user message with the not-ready text."""
    chat_id = event.message.recipient.chat_id
    logger.info("Message from chat %s: %r", chat_id, event.message.body.text)

    await event.message.answer(NOT_READY_TEXT)


# ---------------------------------------------------------------------------
# Entry point used by main.py
# ---------------------------------------------------------------------------
async def run_interaction() -> None:
    """
    Start polling in the current event loop.
    Awaiting this coroutine will block until polling is stopped.
    """
    logger.info("Starting interaction polling...")
    # Optional: delete any existing webhook subscription if present
    try:
        await bot.delete_webhook()
    except Exception:
        pass  # no subscription — fine

    await dp.start_polling(bot)


def start_interaction_sync() -> None:
    """
    Convenience wrapper for running polling from a synchronous context.
    Creates a new event loop if none is running.
    """
    asyncio.run(run_interaction())


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    )
    start_interaction_sync()