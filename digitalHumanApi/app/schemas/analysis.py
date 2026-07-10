from typing import Any, Dict, List, Optional

from pydantic import BaseModel


class AnalysisResultItem(BaseModel):
    dialogue_id: int
    sentiment_label: Optional[str] = None
    satisfaction_score: Optional[int] = None
    concern_tags: Optional[List[str]] = None
    mentioned_spots: Optional[List[str]] = None
    raw_result: Optional[Dict[str, Any]] = None

    model_config = {"from_attributes": True}
