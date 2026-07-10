from datetime import datetime, timezone
from enum import Enum as PyEnum
from typing import Optional

from sqlalchemy import DateTime, Enum, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class DialogueRole(PyEnum):
    user = "user"
    assistant = "assistant"


class Dialogue(Base):
    __tablename__ = "dialogues"

    id: Mapped[int] = mapped_column(primary_key=True)
    visitor_id: Mapped[str] = mapped_column(
        ForeignKey("visitors.id"), index=True
    )
    conversation_id: Mapped[Optional[str]] = mapped_column(String(64), default=None)
    external_message_id: Mapped[Optional[str]] = mapped_column(
        String(64), unique=True, default=None
    )
    content: Mapped[str] = mapped_column(Text, default="")
    role: Mapped[DialogueRole] = mapped_column(
        Enum(DialogueRole, name="dialogue_role"),
        default=DialogueRole.user,
    )
    timestamp: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
    )

    visitor: Mapped["Visitor"] = relationship(back_populates="dialogues")
    evaluation: Mapped[Optional["Evaluation"]] = relationship(
        back_populates="dialogue",
        uselist=False,
    )
