from datetime import datetime
from sqlalchemy import Column, String, Boolean, DateTime, ForeignKey, Index
from sqlalchemy.orm import relationship
from app.database import Base


class User(Base):
    """System user. super_admin can do everything; board_admin is scoped to one board."""
    __tablename__ = "users"

    id = Column(String(36), primary_key=True)
    email = Column(String(300), unique=True, nullable=False, index=True)
    full_name = Column(String(300), nullable=False)
    password_hash = Column(String(200), nullable=False)
    role = Column(String(20), nullable=False, default="board_admin",
                  doc="super_admin | board_admin | program_officer")
    board_id = Column(String(36), ForeignKey("boards.id", ondelete="SET NULL"),
                      nullable=True, doc="NULL for super_admin; set for board_admin")
    is_active = Column(Boolean, default=True, server_default="true",
                       doc="server_default ensures rows inserted by qci_notifications are active")
    created_at = Column(DateTime, default=datetime.utcnow)
    last_login = Column(DateTime, nullable=True)
    external_id = Column(String(100), nullable=True, index=True,
                         doc="QCI portal user ID — used as sync anchor")

    board = relationship("Board")
