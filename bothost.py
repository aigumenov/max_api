"""
bothost.py — diagnostic logger for running the MAX bot on Bothost.

Runs a series of checks and prints/logs each step so you can see exactly
where the startup pipeline stops inside the container.

Run it instead of main.py to debug:
    python bothost.py
"""

import os
import sys
import json
import time
import asyncio
import logging
import traceback
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Logging — to stdout AND to a file (in case stdout is truncated)
# ---------------------------------------------------------------------------
LOG_DIR = Path(os.getenv("DATA_DIR", "/app/data"))
try:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
except Exception:
    LOG_DIR = Path("/tmp")

LOG_FILE = LOG_DIR / "bothost_diagnostic.log"

logger = logging.getLogger("bothost")
logger.setLevel(logging.DEBUG)
logger.handlers.clear()

_formatter = logging.Formatter(
    fmt="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

_stream = logging.StreamHandler(sys.stdout)
_stream.setFormatter(_formatter)
logger.addHandler(_stream)

try:
    _file = logging.FileHandler(LOG_FILE, encoding="utf-8")
    _file.setFormatter(_formatter)
    logger.addHandler(_file)
except Exception as exc:  # noqa: BLE001
    logger.warning("Could not open log file %s: %s", LOG_FILE, exc)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def section(title: str) -> None:
    logger.info("")
    logger.info("=" * 70)
    logger.info(">>> %s", title)
    logger.info("=" * 70)


def ok(msg: str) -> None:
    logger.info("✅ %s", msg)


def warn(msg: str) -> None:
    logger.warning("⚠️  %s", msg)


def fail(msg: str) -> None:
    logger.error("❌ %s", msg)


def mask(value: str | None, keep: int = 6) -> str:
    if not value:
        return "<empty>"
    if len(value) <= keep * 2:
        return value[:keep] + "..."
    return value[:keep] + "..." + value[-keep:]


# ---------------------------------------------------------------------------
# STEP 1 — environment
# ---------------------------------------------------------------------------
def check_environment() -> dict:
    section("STEP 1 — Environment variables & paths")

    env_dump = {
        "MAX_API_TOKEN": mask(os.getenv("MAX_API_TOKEN")),
        "MAX_API_BASE_URL": os.getenv("MAX_API_BASE_URL", "<unset>"),
        "INTERACTIVE": os.getenv("INTERACTIVE", "<unset>"),
        "PORT": os.getenv("PORT", "<unset>"),
        "HOST": os.getenv("HOST", "<unset>"),
        "DATA_DIR": os.getenv("DATA_DIR", "<unset>"),
        "PYTHONPATH": os.getenv("PYTHONPATH", "<unset>"),
    }
    for k, v in env_dump.items():
        logger.info("   %-20s = %s", k, v)

    if not os.getenv("MAX_API_TOKEN"):
        fail("MAX_API_TOKEN is NOT set — the bot cannot start")
    else:
        ok("MAX_API_TOKEN is present")

    logger.info("   CWD           = %s", os.getcwd())
    logger.info("   __file__      = %s", __file__)
    logger.info("   sys.version   = %s", sys.version.replace("\n", " "))
    logger.info("   executable    = %s", sys.executable)

    # list files in CWD to confirm main.py / interaction.py exist
    try:
        files = sorted(os.listdir(os.getcwd()))
        logger.info("   CWD contents  = %s", files)
        for required in ("main.py", "interaction.py"):
            if required in files:
                ok(f"{required} found in CWD")
            else:
                fail(f"{required} NOT found in CWD")
    except Exception as exc:  # noqa: BLE001
        warn(f"Cannot list CWD: {exc}")

    return env_dump


# ---------------------------------------------------------------------------
# STEP 2 — imports
# ---------------------------------------------------------------------------
def check_imports() -> bool:
    section("STEP 2 — Import checks")

    all_ok = True

    modules = [
        "httpx",
        "dotenv",
        "fastapi",
        "uvicorn",
        "maxapi",
    ]
    for mod in modules:
        try:
            __import__(mod)
            ok(f"import {mod}")
        except Exception as exc:  # noqa: BLE001
            fail(f"import {mod} FAILED: {exc}")
            all_ok = False

    # project-local modules
    for mod in ("interaction", "main"):
        try:
            __import__(mod)
            ok(f"import {mod} (project)")
        except Exception as exc:  # noqa: BLE001
            fail(f"import {mod} (project) FAILED: {exc}")
            logger.error(traceback.format_exc())
            all_ok = False

    return all_ok


# ---------------------------------------------------------------------------
# STEP 3 — network / API reachability
# ---------------------------------------------------------------------------
async def check_api() -> bool:
    section("STEP 3 — MAX API reachability")

    import httpx

    token = os.getenv("MAX_API_TOKEN")
    base = os.getenv("MAX_API_BASE_URL", "https://platform-api2.max.ru")
    url = f"{base.rstrip('/')}/me"

    logger.info("   URL = %s", url)
    if not token:
        fail("No token — skipping API call")
        return False

    try:
        t0 = time.time()
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.get(url, headers={"Authorization": token})
        dt = time.time() - t0
        logger.info("   HTTP %s in %.2fs", r.status_code, dt)
        if r.status_code == 200:
            ok(f"API reachable, response: {r.text[:300]}")
            return True
        else:
            fail(f"API returned HTTP {r.status_code}: {r.text[:300]}")
            return False
    except Exception as exc:  # noqa: BLE001
        fail(f"API request FAILED: {exc}")
        logger.error(traceback.format_exc())
        return False


# ---------------------------------------------------------------------------
# STEP 4 — start polling (the actual bot loop)
# ---------------------------------------------------------------------------
async def run_polling() -> None:
    section("STEP 4 — Starting interaction polling")

    try:
        from interaction import run_interaction
    except Exception as exc:  # noqa: BLE001
        fail(f"Cannot import run_interaction: {exc}")
        logger.error(traceback.format_exc())
        return

    logger.info("   run_interaction = %r", run_interaction)
    if not asyncio.iscoroutinefunction(run_interaction):
        fail("run_interaction is NOT async — create_task will fail")

    try:
        logger.info("   Calling run_interaction() ...")
        await run_interaction()
    except Exception as exc:  # noqa: BLE001
        fail(f"Polling crashed: {exc}")
        logger.error(traceback.format_exc())


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------
async def amain() -> None:
    logger.info("")
    logger.info("█" * 70)
    logger.info("  BOTHOST DIAGNOSTIC START — %s",
                datetime.now(timezone.utc).isoformat())
    logger.info("█" * 70)

    check_environment()

    imports_ok = check_imports()

    api_ok = await check_api()

    if not imports_ok or not api_ok:
        fail("Pre-flight checks failed — polling will NOT start.")
        logger.info("Diagnostic log saved to: %s", LOG_FILE)
        return

    ok("Pre-flight checks passed — starting polling.")
    # Keep the process alive by awaiting the polling loop.
    await run_polling()


def main() -> None:
    try:
        asyncio.run(amain())
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
    except Exception as exc:  # noqa: BLE001
        logger.error("FATAL: %s", exc)
        logger.error(traceback.format_exc())
    finally:
        logger.info("Diagnostic log saved to: %s", LOG_FILE)


if __name__ == "__main__":
    main()