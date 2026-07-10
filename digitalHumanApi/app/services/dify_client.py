import logging
from typing import Any, Dict, List, Optional

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)


class DifyClient:
    """Dify API client — Bearer token auth, silent failure on all calls."""

    def __init__(self) -> None:
        self.base_url: str = settings.DIFY_API_URL.rstrip("/")
        self.headers: Dict[str, str] = {
            "Authorization": f"Bearer {settings.DIFY_API_KEY}",
            "Content-Type": "application/json",
        }

    async def get_conversations(self) -> List[Dict[str, Any]]:
        """Fetch all active conversations from Dify. Returns [] on failure."""
        try:
            async with httpx.AsyncClient(
                base_url=self.base_url,
                headers=self.headers,
                timeout=httpx.Timeout(30.0),
            ) as client:
                response = await client.get("/conversations")
                response.raise_for_status()
                data = response.json()
                return data.get("data", [])
        except (httpx.HTTPStatusError, httpx.TimeoutException, httpx.ConnectError) as e:
            logger.warning("Dify get_conversations failed: %s", e)
            return []
        except Exception as e:
            logger.warning("Dify get_conversations unexpected error: %s", e)
            return []

    async def get_conversation_messages(
        self, conversation_id: str
    ) -> List[Dict[str, Any]]:
        """Fetch messages for a conversation. Returns [] on failure."""
        try:
            async with httpx.AsyncClient(
                base_url=self.base_url,
                headers=self.headers,
                timeout=httpx.Timeout(30.0),
            ) as client:
                response = await client.get(
                    f"/conversations/{conversation_id}/messages"
                )
                response.raise_for_status()
                data = response.json()
                return data.get("data", [])
        except (httpx.HTTPStatusError, httpx.TimeoutException, httpx.ConnectError) as e:
            logger.warning(
                "Dify get_messages failed for %s: %s", conversation_id, e
            )
            return []
        except Exception as e:
            logger.warning(
                "Dify get_messages unexpected error for %s: %s", conversation_id, e
            )
            return []

    async def trigger_workflow(
        self, inputs: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """Trigger a Dify workflow. Returns None on failure."""
        try:
            async with httpx.AsyncClient(
                base_url=self.base_url,
                headers=self.headers,
                timeout=httpx.Timeout(60.0),
            ) as client:
                response = await client.post(
                    "/workflows/run", json={"inputs": inputs}
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
