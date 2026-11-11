"""Utilities for invoking async tools from synchronous agent paths."""

import asyncio
import atexit
import concurrent.futures
import contextvars
import functools
import logging
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)

# Shared thread pool for sync tool invocation in async environments.
_SYNC_TOOL_EXECUTOR = concurrent.futures.ThreadPoolExecutor(max_workers=10, thread_name_prefix="tool-sync")

atexit.register(lambda: _SYNC_TOOL_EXECUTOR.shutdown(wait=False))


def make_sync_tool_wrapper(coro: Callable[..., Any], tool_name: str) -> Callable[..., Any]:
    """Build a synchronous wrapper while preserving the tool signature."""

    def run_coroutine(*args: Any, **kwargs: Any) -> Any:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        try:
            if loop is not None and loop.is_running():
                context = contextvars.copy_context()
                future = _SYNC_TOOL_EXECUTOR.submit(
                    context.run,
                    lambda: asyncio.run(coro(*args, **kwargs)),
                )
                return future.result()
            return asyncio.run(coro(*args, **kwargs))
        except Exception as exc:
            logger.error(
                "Error invoking tool %r via sync wrapper: %s",
                tool_name,
                exc,
                exc_info=True,
            )
            raise

    @functools.wraps(coro)
    def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
        return run_coroutine(*args, **kwargs)

    return sync_wrapper
