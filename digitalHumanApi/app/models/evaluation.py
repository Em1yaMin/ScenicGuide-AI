from __future__ import annotations

from typing import Any, Dict, List, Optional, TYPE_CHECKING

from sqlalchemy import ForeignKey, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base

if TYPE_CHECKING:
    from app.models.dialogue import Dialogue


class Evaluation(Base):
    __tablename__ = "evaluations"

    id: Mapped[int] = mapped_column(primary_key=True)
    dialogue_id: Mapped[int] = mapped_column(
        ForeignKey("dialogues.id"),
        unique=True,
        index=True,
    )
    sentiment_label: Mapped[Optional[str]] = mapped_column(String(64), default=None)
    satisfaction_score: Mapped[Optional[int]] = mapped_column(Integer, default=None)
    concern_tags: Mapped[Optional[List[str]]] = mapped_column(JSON, default=None)
    mentioned_spots: Mapped[Optional[List[str]]] = mapped_column(JSON, default=None)
    raw_result: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSON, default=None)

    dialogue: Mapped["Dialogue"] = relationship(back_populates="evaluation")
