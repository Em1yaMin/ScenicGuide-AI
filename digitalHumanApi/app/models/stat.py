from datetime import date as date_type, datetime, timezone
from typing import Any, Dict, Optional

from sqlalchemy import Date, DateTime, JSON, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class Stat(Base):
    __tablename__ = "stats"

    id: Mapped[int] = mapped_column(primary_key=True)
    date: Mapped[date_type] = mapped_column(Date, index=True)
    metric_type: Mapped[str] = mapped_column(String(64), index=True)
    metric_value: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSON, default=None)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
    )
