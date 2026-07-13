import asyncio
import logging
from typing import Any, Awaitable, Callable, Dict, List, Optional, TypeVar

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

T = TypeVar("T")


async def _retry_silent(
    description: str,
    fn: Callable[[], Awaitable[T]],
    default: T,
    max_retries: int = 3,
) -> T:
    """Run an idempotent async call with exponential backoff.

    Preserves the existing silent-failure contract: transient HTTP errors are
    retried, and the caller-supplied default is returned after exhaustion or
    on any unexpected exception.
    """
    for attempt in range(max_retries):
        try:
            return await fn()
        except (httpx.HTTPStatusError, httpx.TimeoutException, httpx.ConnectError) as e:
            if attempt < max_retries - 1:
                wait = 0.5 * (2 ** attempt)
                logger.warning(
                    "%s failed (attempt %d/%d): %s; retrying in %.1fs",
                    description,
                    attempt + 1,
                    max_retries,
                    e,
                    wait,
                )
                await asyncio.sleep(wait)
            else:
                logger.warning(
                    "%s failed after %d attempts: %s",
                    description,
                    max_retries,
                    e,
                )
        except Exception as e:
            logger.warning(
                "%s unexpected error (attempt %d/%d): %s",
                description,
                attempt + 1,
                max_retries,
                e,
            )
            return default
    return default


class DifyClient:
    """Dify API client.

    Uses two API keys:
      - DIFY_API_KEY (Chat Agent): for /conversations and /messages
      - DIFY_WORKFLOW_API_KEY (Workflow app): for /workflows/run
    Both share the same base_url but are separate Dify applications.
    """

    def __init__(self) -> None:
        self.base_url: str = settings.DIFY_API_URL.rstrip("/")
        self.headers: Dict[str, str] = {
            "Authorization": f"Bearer {settings.DIFY_API_KEY}",
            "Content-Type": "application/json",
        }
        self.workflow_headers: Dict[str, str] = {
            "Authorization": f"Bearer {settings.DIFY_WORKFLOW_API_KEY}",
            "Content-Type": "application/json",
        }

    async def get_conversations(
        self, user: str, limit: int = 100
    ) -> List[Dict[str, Any]]:
        """Fetch conversations for a specific user from Dify Chat Agent.

        Dify API: GET /conversations?user=xxx
        Returns [] on failure.
        """
        async def _fetch() -> List[Dict[str, Any]]:
            async with httpx.AsyncClient(
                base_url=self.base_url,
                headers=self.headers,
                timeout=httpx.Timeout(30.0),
            ) as client:
                response = await client.get(
                    "/conversations", params={"user": user, "limit": limit}
                )
                response.raise_for_status()
                data = response.json()
                return data.get("data", [])

        return await _retry_silent(
            f"Dify get_conversations for user={user}", _fetch, []
        )

    async def get_conversation_messages(
        self, conversation_id: str, user: str, limit: int = 100
    ) -> List[Dict[str, Any]]:
        """Fetch messages for a conversation from Dify Chat Agent.

        Dify API: GET /messages?conversation_id=xxx&user=xxx
        (NOT /conversations/{id}/messages — that path does not exist)
        Returns [] on failure.
        """
        async def _fetch() -> List[Dict[str, Any]]:
            async with httpx.AsyncClient(
                base_url=self.base_url,
                headers=self.headers,
                timeout=httpx.Timeout(30.0),
            ) as client:
                response = await client.get(
                    "/messages",
                    params={
                        "conversation_id": conversation_id,
                        "user": user,
                        "limit": limit,
                    },
                )
                response.raise_for_status()
                data = response.json()
                return data.get("data", [])

        return await _retry_silent(
            f"Dify get_messages for conv={conversation_id}", _fetch, []
        )

    async def trigger_workflow(
        self, inputs: Dict[str, Any], user: str = "system"
    ) -> Optional[Dict[str, Any]]:
        """Trigger a Dify Workflow application.

        Uses DIFY_WORKFLOW_API_KEY (separate from Chat Agent key).
        Dify API: POST /workflows/run
        Returns None on failure.
        """
        if not settings.DIFY_WORKFLOW_API_KEY:
            logger.warning(
                "DIFY_WORKFLOW_API_KEY not configured, skipping workflow trigger"
            )
            return None

        try:
            async with httpx.AsyncClient(
                base_url=self.base_url,
                headers=self.workflow_headers,
                timeout=httpx.Timeout(60.0),
            ) as client:
                response = await client.post(
                    "/workflows/run",
                    json={"inputs": inputs, "response_mode": "blocking", "user": user},
                )
                response.raise_for_status()
                return response.json()
        except (httpx.HTTPStatusError, httpx.TimeoutException, httpx.ConnectError) as e:
            logger.warning("Dify trigger_workflow failed: %s", e)
            return None
        except Exception as e:
            logger.warning("Dify trigger_workflow unexpected error: %s", e)
            return None


dify_client = DifyClient()
