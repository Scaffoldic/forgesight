"""``sdk_lifespan`` — configure the SDK on startup, flush cleanly on shutdown.

The SDK buffers records and flushes on a timer (feat-003); a rolling deploy / SIGTERM that
stops the process drops the in-flight batch unless someone flushes. ASGI servers run the
lifespan shutdown phase on SIGTERM, so wiring this guarantees "telemetry is not lost on a
clean deploy" by installation, not by discipline.

Usable directly (``FastAPI(lifespan=sdk_lifespan)``) or composed inside a user lifespan
(``async with sdk_lifespan(app): ...``). ``**configure_kwargs`` flow to ``configure()``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from forgesight_core import configure, get_runtime


@asynccontextmanager
async def sdk_lifespan(
    app: Any = None,
    *,
    configure_sdk: bool = True,
    flush_timeout_millis: int | None = None,
    **configure_kwargs: Any,
) -> AsyncIterator[None]:
    """Lifespan: ``configure()`` on startup; ``force_flush()`` + ``shutdown()`` on shutdown.

    ``configure_sdk=False`` skips startup configuration (respect an already-configured SDK).
    ``flush_timeout_millis`` defaults to the runtime's bounded ``export_timeout_millis`` so a
    wedged backend can't hang the shutdown.
    """
    if configure_sdk:
        configure(**configure_kwargs)
    try:
        yield
    finally:
        runtime = get_runtime()
        timeout = (
            flush_timeout_millis
            if flush_timeout_millis is not None
            else runtime.config.export_timeout_millis
        )
        runtime.force_flush(timeout)
        runtime.shutdown(timeout)
