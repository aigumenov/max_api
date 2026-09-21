"""
FastAPI app to verify connection to the MAX messenger API
and start the bot interaction loop.

- Locally: prints status and (optionally) waits for ENTER.
- On Bothost / container: skips the CLI wait, starts polling, serves /health.
"""

import os
import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.responses import JSONResponse

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
# Only load .env if the file actually exists (safe inside containers).
_env_file = Path(__file__).parent / ".env"
if _env_file.exists():
    load_dotenv(_env_file)
else:
    load_dotenv()  # no-op, harmless

MAX_API_TOKEN: str | None = os.getenv("MAX_API_TOKEN")
MAX_API_BASE_URL: str = os.getenv("MAX_API_BASE_URL", "https://platform-api2.max.ru")

# When true (local dev), wait for ENTER on shutdown. Off on Bothost.
INTERACTIVE: bool = os.getenv("INTERACTIVE", "false").lower() in ("1", "true", "yes")

REQUEST_TIMEOUT = 15.0

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
)
logger = logging.getLogger("max-api-connect")


# ---------------------------------------------------------------------------
# MAX API check
# ---------------------------------------------------------------------------
async def check_max_api_connection() -> tuple[bool, int | None, dict | str | None]:
    if not MAX_API_TOKEN:
        return False, None, "MAX_API_TOKEN is not set in environment variables"

    url = f"{MAX_API_BASE_URL.rstrip('/')}/me"
    headers = {"Authorization": MAX_API_TOKEN}

    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
            r = await client.get(url, headers=headers)
    except httpx.RequestError as exc:
        return False, None, f"Network error: {exc}"

    if r.status_code == 200:
        try:
            return True, r.status_code, r.json()
        except ValueError:
            return True, r.status_code, r.text

    try:
        body = r.json()
    except ValueError:
        body = r.text
    return False, r.status_code, body


def report_connection(success: bool, status: int | None, payload) -> None:
    logger.info("=" * 60)
    if success:
        logger.info("✅ CONNECTION OK | HTTP %s", status)
        if isinstance(payload, dict):
            logger.info("   Bot name : %s", payload.get("name"))
            logger.info("   Username : @%s", payload.get("username"))
            logger.info("   Bot ID   : %s", payload.get("user_id") or payload.get("id"))
        else:
            logger.info("   Response : %s", payload)
    else:
        logger.error("❌ CONNECTION FAILED | HTTP %s", status)
        logger.error("   Details  : %s", payload)
    logger.info("=" * 60)


async def wait_for_user_input() -> None:
    """Interactive wait — only used locally. Never called in container mode."""
    loop = asyncio.get_running_loop()
    try:
        await loop.run_in_executor(
            None, lambda: input("Press ENTER to shut down... ")
        )
    except (EOFError, KeyboardInterrupt):
        pass


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting up — checking MAX API connection...")
    success, status, payload = await check_max_api_connection()
    report_connection(success, status, payload)

    # --- optional interactive wait (local dev only) ------------------------
    app.state.cli_task = None
    if INTERACTIVE:
        logger.info("Interactive mode ON — waiting for user input.")
        app.state.cli_task = asyncio.create_task(wait_for_user_input())

    # --- start the bot polling loop if the connection is OK ----------------
    app.state.polling_task = None
    if success:
        try:
            from interaction import run_interaction

            app.state.polling_task = asyncio.create_task(run_interaction())
            logger.info("Bot interaction polling task started.")
        except Exception as exc:  # noqa: BLE001
            logger.exception("Failed to start interaction polling: %s", exc)

    yield  # <-- app is running here

    # --- shutdown ----------------------------------------------------------
    logger.info("Shutting down...")

    if app.state.polling_task:
        app.state.polling_task.cancel()
        try:
            await app.state.polling_task
        except asyncio.CancelledError:
            pass
        except Exception as exc:  # noqa: BLE001
            logger.warning("Polling task ended with error: %s", exc)

    if app.state.cli_task:
        app.state.cli_task.cancel()
        try:
            await app.state.cli_task
        except (asyncio.CancelledError, Exception):
            pass


app = FastAPI(title="MAX API Connect Checker", version="1.2.0", lifespan=lifespan)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------
@app.get("/")
async def root():
    return {"service": "max-api-connect", "status": "running"}


@app.get("/health")
async def health():
    success, status, payload = await check_max_api_connection()
    return JSONResponse(
        status_code=200 if success else 503,
        content={
            "ok": success,
            "status_code": status,
            "payload": payload if isinstance(payload, (dict, list, str)) else str(payload),
        },
    )


# ---------------------------------------------------------------------------
# Entrypoint — bind to 0.0.0.0 and $PORT for Bothost
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import uvicorn

    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", "8000"))

    logger.info("Uvicorn binding to %s:%s", host, port)
    uvicorn.run("main:app", host=host, port=port, reload=False)