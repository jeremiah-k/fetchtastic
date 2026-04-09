"""Tests for global network blocking behavior in the test environment."""

import pytest
import requests


def test_requests_request_blocked():
    """Top-level requests.request should be blocked unless a test mocks it."""
    with pytest.raises(RuntimeError, match="Network access is blocked during tests"):
        requests.request("GET", "https://example.com")


def test_requests_session_send_blocked():
    """Session.send should be blocked to prevent bypassing Session.request patches."""
    prepared = requests.Request("GET", "https://example.com").prepare()
    with pytest.raises(RuntimeError, match="Network access is blocked during tests"):
        requests.Session().send(prepared)


@pytest.mark.asyncio
async def test_aiohttp_request_blocked():
    """aiohttp.request should be blocked unless a test explicitly mocks async HTTP."""
    aiohttp = pytest.importorskip("aiohttp")
    request_call = aiohttp.request("GET", "https://example.com")
    with pytest.raises(
        RuntimeError, match="Async network access is blocked during tests"
    ):
        if hasattr(request_call, "__aenter__"):
            async with request_call:
                pass
        else:
            await request_call
