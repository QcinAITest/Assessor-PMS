from datetime import datetime
from sqlalchemy import Column, String, Boolean, DateTime, ForeignKey
from sqlalchemy.orm import relationship
from app.database import Base


class User(Base):
    """System user. SYSTEM_ADMIN can do everything; BOARD_ADMIN is scoped to one board."""
    __tablename__ = "users"

    id = Column(String(36), primary_key=True)
    email = Column(String(300), unique=True, nullable=False, index=True)
    full_name = Column(String(300), nullable=False)
    password_hash = Column(String(200), nullable=False)
    role = Column(String(20), nullable=False, default="BOARD_ADMIN",
                  doc="SYSTEM_ADMIN | BOARD_ADMIN")
    board_id = Column(String(36), ForeignKey("boards.id", ondelete="SET NULL"),
                      nullable=True, doc="NULL for SYSTEM_ADMIN; set for BOARD_ADMIN")
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    last_login = Column(DateTime, nullable=True)

    board = relationship("Board")
