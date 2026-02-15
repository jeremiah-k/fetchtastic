"""
Shared async test utilities.
"""

from collections.abc import AsyncIterator, Iterable
from typing import TypeVar

T = TypeVar("T")


async def make_async_iter(items: Iterable[T]) -> AsyncIterator[T]:
    """
    Create an async iterator that yields the elements of a synchronous iterable.

    Args:
        items: A synchronous iterable whose elements will be yielded asynchronously.

    Returns:
        AsyncIterator[T]: An async iterator that yields each element from the provided iterable.
    """
    for item in items:
        yield item
