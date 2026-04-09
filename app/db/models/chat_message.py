# File: app/db/models/chat_message.py
"""
DisasterChatSession — SQLAlchemy model for group chat chunks.

Each row = one chunk of messages (up to 50) stored as a JSONB array.

Table: disaster_chat_sessions
  id           → UUID primary key (from Base)
  disaster_id  → which disaster
  chunk_number → 1, 2, 3... increments per disaster
  messages     → JSONB array of message dicts (max 50 per chunk)
  from_seq     → sequence number of first message in this chunk
  to_seq       → sequence number of last message in this chunk
  created_at   → when this chunk was created (from Base)

Each message dict inside the JSONB array:
  {
    "id":          "uuid",
    "seq":         1,
    "sender_id":   "uuid",
    "sender_name": "John Doe",
    "sender_type": "admin",
    "message":     "Fire contained on north side",
    "sent_at":     "2026-04-02T10:30:00"
  }

Example DB rows for one disaster with 130 messages:
  chunk 1 → from_seq:1,   to_seq:50,  messages: [{msg1}...{msg50}]
  chunk 2 → from_seq:51,  to_seq:100, messages: [{msg51}...{msg100}]
  chunk 3 → from_seq:101, to_seq:130, messages: [{msg101}...{msg130}]  ← partial (last chunk)
"""

from sqlalchemy import String, Integer, Index
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.dialects.postgresql import JSONB

from app.db.models.base import Base


class DisasterChatSession(Base):
    """
    One chunk of chat messages for a disaster.

    Bulk insert strategy:
      - Messages are buffered in memory per disaster
      - When buffer hits 50 OR 30 seconds pass OR last user disconnects
        → entire buffer is written as one new chunk row (INSERT)
      - Each chunk is immutable after creation (no UPDATE)
      - Reading history = fetch all chunks ordered by chunk_number
    """

    __tablename__ = "disaster_chat_sessions"

    # Which disaster this chunk belongs to
    disaster_id: Mapped[str] = mapped_column(
        String(36),
        nullable=False,
        index=True,
    )

    # Chunk sequence — 1, 2, 3... per disaster
    chunk_number: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    # Sequence number of first message in this chunk
    from_seq: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    # Sequence number of last message in this chunk
    to_seq: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    # JSONB array of message dicts — max 50 per chunk
    # Each element: {id, seq, sender_id, sender_name, sender_type, message, sent_at}
    messages: Mapped[list] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
    )

    # Composite index for fast history queries per disaster
    __table_args__ = (
        Index("ix_chat_session_disaster_chunk", "disaster_id", "chunk_number"),
    )

    def __repr__(self) -> str:
        return (
            f"<DisasterChatSession("
            f"disaster={self.disaster_id}, "
            f"chunk={self.chunk_number}, "
            f"seq={self.from_seq}-{self.to_seq}"
            f")>"
        )