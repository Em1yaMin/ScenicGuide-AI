from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class KnowledgeItem(BaseModel):
    id: int
    title: str
    category: str
    content: str
    tags: Optional[list[str]] = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class KnowledgeCreateRequest(BaseModel):
    title: str
    category: str
    content: str
    tags: Optional[list[str]] = None


class KnowledgeUpdateRequest(BaseModel):
    title: Optional[str] = None
    category: Optional[str] = None
    content: Optional[str] = None
    tags: Optional[list[str]] = None


class KnowledgeListResponse(BaseModel):
    items: list[KnowledgeItem]
    total: int
    page: int
    page_size: int

    model_config = {"from_attributes": True}


class KnowledgeImportResponse(BaseModel):
    imported_count: int
    skipped_count: int = 0

    model_config = {"from_attributes": True}
