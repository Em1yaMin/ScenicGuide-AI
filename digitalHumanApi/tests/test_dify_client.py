import httpx
import pytest

from app.services.dify_client import _retry_silent


@pytest.mark.asyncio
async def test_retry_silent_succeeds_first_attempt():
    async def _fn():
        return "ok"

    result = await _retry_silent("test", _fn, "default")
    assert result == "ok"


@pytest.mark.asyncio
async def test_retry_silent_returns_default_after_retries():
    async def _fn():
        raise httpx.ConnectError("boom")

    result = await _retry_silent("test", _fn, "default", max_retries=2)
    assert result == "default"
