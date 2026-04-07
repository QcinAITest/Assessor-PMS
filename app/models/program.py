from datetime import datetime
from sqlalchemy import Column, String, Integer, Boolean, Text, DateTime, ForeignKey
from sqlalchemy.orm import relationship
from app.database import Base


class ServiceLine(Base):
    """
    A service line within a board.
    Each board may use different terminology:
      NABL: 'Testing Laboratories' / 'Medical Laboratories'
      NABH: 'Hospitals' / 'SHCO' / 'Blood Banks'
      NABCB: 'Certification Bodies' / 'Inspection Bodies'
    """
    __tablename__ = "service_lines"

    id = Column(String(36), primary_key=True)
    board_id = Column(String(36), ForeignKey("boards.id", ondelete="CASCADE"), nullable=False)
    code = Column(String(50), nullable=False)
    name = Column(String(300), nullable=False)
    description = Column(Text)
    sort_order = Column(Integer, default=0)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    board = relationship("Board", back_populates="service_lines")
    programs = relationship("Program", back_populates="service_line",
                            cascade="all, delete-orphan", order_by="Program.sort_order")


class Program(Base):
    """
    A program (scheme) within a service line.
    e.g. ServiceLine='Testing Laboratories' → Program='ISO/IEC 17025 Testing'
    """
    __tablename__ = "programs"

    id = Column(String(36), primary_key=True)
    service_line_id = Column(String(36), ForeignKey("service_lines.id", ondelete="CASCADE"),
                              nullable=False)
    board_id = Column(String(36), ForeignKey("boards.id"), nullable=False)
    code = Column(String(50), nullable=False)
    name = Column(String(300), nullable=False)
    description = Column(Text)
    standard_version = Column(String(200), nullable=True,
                               doc="e.g. 'ISO/IEC 17025:2017'")
    sort_order = Column(Integer, default=0)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    service_line = relationship("ServiceLine", back_populates="programs")
    board = relationship("Board")
