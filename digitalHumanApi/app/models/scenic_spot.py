from typing import Any, Dict, List, Optional

from sqlalchemy import JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class ScenicSpot(Base):
    __tablename__ = "scenic_spots"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    description: Mapped[Optional[str]] = mapped_column(Text, default=None)
    age_recommendation: Mapped[Optional[Dict[str, Any]]] = mapped_column(
        JSON, default=None
    )
    route_tags: Mapped[Optional[List[str]]] = mapped_column(JSON, default=None)
