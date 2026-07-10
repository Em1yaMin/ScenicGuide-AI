from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class VisitorRegisterRequest(BaseModel):
    visitor_id: str
    device_info: Optional[str] = None


class VisitorRegisterResponse(BaseModel):
    visitor_id: str
    is_new: bool
    first_visit: datetime
    last_visit: datetime

    model_config = {"from_attributes": True}
