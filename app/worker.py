"""Background worker: webhook delivery, session expiry, and data purge.

Run one instance per deployment::

    python -m app.worker

Every ``WORKER_INTERVAL_SECONDS`` it

1. expires sessions that were not finished in time (queuing their
   ``session.expired`` events),
2. sends webhook deliveries that are due, retrying failures with
   backoff, and
3. deletes session results whose retention window has ended.

After each successful round it touches :data:`HEARTBEAT_FILE`; the
container health check (``python -m app.worker --check``) reports
unhealthy when the file is older than :data:`HEARTBEAT_MAX_AGE_SECONDS`,
for example because the database has been unreachable for a while.

Deliveries are not locked between processes, so running two workers at
once could send an event twice. Receivers should still treat the
``X-EveryCRED-Delivery`` id as idempotency key, as retries after a lost
response can repeat an event anyway.
"""

import asyncio
import logging
import signal
import sys
import tempfile
import time
from contextlib import suppress
from pathlib import Path

import httpx
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import app.connectors  # noqa: F401 - imports connector modules so they register
from app.connectors.registry import default_registry
from app.core.config import Settings, get_settings
from app.core.database import create_database_engine, create_session_factory
from app.core.http_client import create_http_client
from app.core.logging import configure_logging
from app.core.secret_store import build_secret_store
from app.features.sessions.dependencies import (
    build_session_service,
    build_webhook_service,
)

logger = logging.getLogger("app.worker")

BATCH_SIZE = 50
# /tmp is the only writable path in the read-only container.
HEARTBEAT_FILE = Path(tempfile.gettempdir()) / "everycred-worker-heartbeat"
HEARTBEAT_MAX_AGE_SECONDS = 120


async def run_once(
    session_factory: async_sessionmaker[AsyncSession],
    http_client: httpx.AsyncClient,
    settings: Settings,
) -> dict[str, int]:
    """Do one round of work and return what was done, by task."""
    done: dict[str, int] = {}
    # Separate units of work, so a failure in one task does not undo
    # or block the others.
    async with session_factory() as session:
        sessions = build_session_service(
            session,
            default_registry,
            build_secret_store(session, settings),
            http_client,
            settings,
        )
        done["expired"] = await sessions.expire_due(BATCH_SIZE)
    async with session_factory() as session:
        webhooks = build_webhook_service(
            session,
            build_secret_store(session, settings),
            http_client,
            settings,
        )
        done["delivered"] = await webhooks.deliver_due(BATCH_SIZE)
    async with session_factory() as session:
        sessions = build_session_service(
            session,
            default_registry,
            build_secret_store(session, settings),
            http_client,
            settings,
        )
        done["purged"] = await sessions.purge_results(BATCH_SIZE)
    return done


async def run_forever(stop: asyncio.Event) -> None:
    """Loop until ``stop`` is set, logging (not raising) failed rounds."""
    settings = get_settings()
    engine = create_database_engine(settings)
    session_factory = create_session_factory(engine)
    http_client = create_http_client(settings)
    logger.info(
        "Worker started; running every %s s", settings.worker_interval_seconds
    )
    try:
        while not stop.is_set():
            try:
                done = await run_once(session_factory, http_client, settings)
                HEARTBEAT_FILE.touch()
                if any(done.values()):
                    logger.info("Worker round: %s", done)
            except Exception:
                # A database or network blip must not kill the worker;
                # the next round tries again.
                logger.exception("Worker round failed")
            with suppress(TimeoutError):
                await asyncio.wait_for(
                    stop.wait(), timeout=settings.worker_interval_seconds
                )
    finally:
        await http_client.aclose()
        await engine.dispose()
        logger.info("Worker stopped")


def is_healthy(now: float | None = None) -> bool:
    """Return True if a round succeeded within the allowed age."""
    try:
        last_round = HEARTBEAT_FILE.stat().st_mtime
    except FileNotFoundError:
        return False
    current = time.time() if now is None else now
    return current - last_round <= HEARTBEAT_MAX_AGE_SECONDS


def main() -> None:
    """Entry point: configure logging and stop cleanly on SIGTERM.

    With ``--check``, exit 0 or 1 by :func:`is_healthy` instead; used by
    the container health check.
    """
    if "--check" in sys.argv[1:]:
        sys.exit(0 if is_healthy() else 1)
    settings = get_settings()
    configure_logging(settings.log_level, use_json=settings.log_json)

    async def runner() -> None:
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for signal_number in (signal.SIGINT, signal.SIGTERM):
            # Not supported on Windows event loops; Ctrl+C still works
            # there through KeyboardInterrupt.
            with suppress(NotImplementedError):
                loop.add_signal_handler(signal_number, stop.set)
        await run_forever(stop)

    with suppress(KeyboardInterrupt):
        asyncio.run(runner())


if __name__ == "__main__":
    main()
