from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class DialogueItem(BaseModel):
    id: int
    visitor_id: str
    conversation_id: Optional[str] = None
    content: str
    role: str
    timestamp: datetime

    model_config = {"from_attributes": True}
