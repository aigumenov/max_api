"""
FastAPI application to verify connection to the MAX messenger API.

On startup it performs a GET /me request using the token from the env.
Prints the status to the CLI and then waits for user input.
"""

import os
import sys
import asyncio
import logging
from contextlib import asynccontextmanager

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
load_dotenv()

MAX_API_TOKEN: str | None = os.getenv("MAX_API_TOKEN")
MAX_API_BASE_URL: str = os.getenv("MAX_API_BASE_URL", "https://platform-api2.max.ru")

# MAX rate limit: 30 rps. We stay well below it.
REQUEST_TIMEOUT = 15.0  # seconds

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
)
logger = logging.getLogger("max-api-connect")


# ---------------------------------------------------------------------------
# API connection check
# ---------------------------------------------------------------------------
async def check_max_api_connection() -> tuple[bool, int | None, dict | str | None]:
    """
    Calls GET /me on the MAX API to verify the bot token.

    Returns:
        (success, status_code, payload_or_error)
    """
    if not MAX_API_TOKEN:
        return False, None, "MAX_API_TOKEN is not set in the environment (.env)"

    url = f"{MAX_API_BASE_URL.rstrip('/')}/me"
    headers = {"Authorization": MAX_API_TOKEN}

    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
            response = await client.get(url, headers=headers)
    except httpx.RequestError as exc:
        return False, None, f"Network error: {exc}"

    status = response.status_code

    if status == 200:
        try:
            return True, status, response.json()
        except ValueError:
            return True, status, response.text

    # Try to surface a helpful error body
    try:
        body = response.json()
    except ValueError:
        body = response.text

    return False, status, body


# ---------------------------------------------------------------------------
# CLI reporting
# ---------------------------------------------------------------------------
def report_connection(success: bool, status: int | None, payload) -> None:
    print("\n" + "=" * 60)
    if success:
        print("✅ CONNECTION OK")
        print(f"   HTTP status: {status}")
        if isinstance(payload, dict):
            name = payload.get("name") or payload.get("username")
            username = payload.get("username")
            bot_id = payload.get("user_id") or payload.get("id")
            print(f"   Bot name : {name}")
            print(f"   Username : @{username}" if username else "   Username : n/a")
            print(f"   Bot ID   : {bot_id}")
        else:
            print(f"   Response : {payload}")
    else:
        print("❌ CONNECTION FAILED")
        print(f"   HTTP status: {status if status is not None else 'N/A'}")
        print(f"   Details    : {payload}")
    print("=" * 60 + "\n")


async def wait_for_user_input() -> None:
    """
    Wait for the user to type something in the terminal.
    Runs in a thread so it doesn't block the event loop.
    """
    loop = asyncio.get_running_loop()
    try:
        await loop.run_in_executor(
            None,
            lambda: input("Press ENTER (or type 'q' + ENTER) to shut down... "),
        )
    except (EOFError, KeyboardInterrupt):
        pass


# ---------------------------------------------------------------------------
# FastAPI lifespan
# ---------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting up — checking MAX API connection...")
    success, status, payload = await check_max_api_connection()
    report_connection(success, status, payload)

    # Kick off the CLI wait in the background so it doesn't block startup.
    cli_task = asyncio.create_task(wait_for_user_input())
    app.state.cli_task = cli_task

    yield

    # Shutdown
    logger.info("Shutting down...")
    cli_task.cancel()
    try:
        await cli_task
    except (asyncio.CancelledError, Exception):
        pass


app = FastAPI(
    title="MAX API Connect Checker",
    version="1.0.0",
    lifespan=lifespan,
)


# ---------------------------------------------------------------------------
# Optional endpoints
# ---------------------------------------------------------------------------
@app.get("/")
async def root():
    return {"service": "max-api-connect", "status": "running"}


@app.get("/health")
async def health():
    """Re-run the MAX API check on demand."""
    success, status, payload = await check_max_api_connection()
    return {
        "ok": success,
        "status_code": status,
        "payload": payload if isinstance(payload, (dict, list, str)) else str(payload),
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=False)