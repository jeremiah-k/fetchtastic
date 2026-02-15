"""
Shared async test utilities.
"""

from collections.abc import AsyncIterator, Iterable
from typing import TypeVar

T = TypeVar("T")


async def make_async_iter(items: Iterable[T]) -> AsyncIterator[T]:
    """
    Yield each item from a synchronous iterable as an async iterator.

    Parameters:
        items (Iterable[T]): Items to yield.

    Returns:
        AsyncIterator[T]: Async iterator over the provided items.
    """
    for item in items:
        yield item
