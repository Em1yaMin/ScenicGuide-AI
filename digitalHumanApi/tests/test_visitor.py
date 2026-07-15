import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_register_visitor(client: AsyncClient):
    """Visitor register endpoint smoke test — placeholder kept after
    removing recommend / spot-detail features (see ADR-004)."""
    response = await client.post(
        "/api/v1/visitor/register",
        json={"visitor_id": "test-visitor-1"},
    )
    assert response.status_code in (200, 201)
