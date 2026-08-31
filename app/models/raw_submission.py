from datetime import datetime
from sqlalchemy import (
    Column, Integer, String, Text, DateTime, Boolean, JSON, Index
)
from sqlalchemy.dialects.postgresql import JSONB as PG_JSONB
from app.database import Base

JSONB = JSON().with_variant(PG_JSONB, "postgresql")


class RawFormSubmission(Base):
    """
    Stores raw, semi-structured form submissions (e.g. from historical data files,
    legacy portals, or un-normalized multi-section JSON feedback).
    """
    __tablename__ = "raw_form_submissions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    legacy_id = Column(Integer, nullable=True, index=True)
    board_code = Column(String(20), default="NABH", nullable=True, index=True)
    user_name = Column(String(255), nullable=True, index=True)
    role = Column(String(100), nullable=True, index=True)
    hospital_name = Column(String(500), nullable=True)
    other_remark = Column(Text, nullable=True)
    form_data = Column(JSONB, nullable=False)
    raw_payload = Column(JSONB, nullable=True)
    submitted_at = Column(DateTime, nullable=True, index=True)
    is_processed = Column(Boolean, default=False, nullable=False, index=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        Index("idx_raw_submissions_board", "board_code"),
        Index("idx_raw_submissions_role", "role"),
        Index("idx_raw_submissions_user_name", "user_name"),
        Index("idx_raw_submissions_legacy_id", "legacy_id"),
        Index("idx_raw_submissions_submitted_at", "submitted_at"),
        Index("idx_raw_submissions_is_processed", "is_processed"),
    )
