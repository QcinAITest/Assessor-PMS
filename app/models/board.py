import json
from datetime import datetime
from sqlalchemy import (
    Column, Integer, String, Float, Boolean, Text, DateTime, ForeignKey, JSON,
    UniqueConstraint, CheckConstraint, Enum as SAEnum
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship
from app.database import Base


class Board(Base):
    """
    Top-level entity: NABL, NABH, NABCB, NABET, etc.
    All board-specific behaviour is driven by `config` JSON — no if/else branches.
    """
    __tablename__ = "boards"

    id = Column(String(36), primary_key=True)
    code = Column(String(20), unique=True, nullable=False, index=True)
    name = Column(String(200), nullable=False)
    description = Column(Text)
    logo_url = Column(String(500))
    is_active = Column(Boolean, default=True)
    config = Column(JSONB, nullable=False, server_default="{}",
                    doc="Master board profile JSON — server_default ensures cross-app INSERT safety")
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    roles = relationship("BoardRole", back_populates="board", cascade="all, delete-orphan")
    form_templates = relationship("FormTemplate", back_populates="board", cascade="all, delete-orphan")
    frequency_rules = relationship("FrequencyRule", back_populates="board", cascade="all, delete-orphan")
    webhooks = relationship("Webhook", back_populates="board", cascade="all, delete-orphan")
    portal_adapters = relationship("PortalAdapter", back_populates="board", cascade="all, delete-orphan")
    service_lines = relationship("ServiceLine", back_populates="board", cascade="all, delete-orphan")

    @property
    def rating_engine(self):
        return self.config.get("rating_engine", "numeric")

    @property
    def star_bands(self):
        return self.config.get("star_bands", [])

    @property
    def stakeholder_weights(self):
        return self.config.get("stakeholder_weights", {})


class BoardRole(Base):
    """
    Maps generic system role IDs to board-specific labels.
    e.g. ROLE_LEAD -> 'Lead Assessor' (NABCB) or 'Principal Assessor' (NABH)
    """
    __tablename__ = "board_roles"
    __table_args__ = (UniqueConstraint("board_id", "system_role_id"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    board_id = Column(String(36), ForeignKey("boards.id", ondelete="CASCADE"), nullable=False)
    system_role_id = Column(String(50), nullable=False, index=True)
    display_label = Column(String(200), nullable=False)
    description = Column(Text)
    can_be_evaluator = Column(Boolean, default=False)
    can_be_evaluee = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    board = relationship("Board", back_populates="roles")


class FormTemplate(Base):
    """
    A feedback form definition. Each board has N form templates.
    The `stakeholder_weight` determines how much this form contributes to the Final Audit Score.
    """
    __tablename__ = "form_templates"
    __table_args__ = (UniqueConstraint("board_id", "code"),)

    id = Column(String(36), primary_key=True)
    board_id = Column(String(36), ForeignKey("boards.id", ondelete="CASCADE"), nullable=False)
    code = Column(String(50), nullable=False)
    name = Column(String(300), nullable=False)
    description = Column(Text)
    stakeholder_weight = Column(Float, nullable=False, doc="Decimal, e.g. 0.30 = 30%")
    target_evaluator_role = Column(String(50), doc="Which role fills this form")
    target_evaluee_roles = Column(JSONB, doc="List of roles this form evaluates")
    is_mandatory = Column(Boolean, default=True)
    is_active = Column(Boolean, default=True)
    version = Column(Integer, default=1)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    board = relationship("Board", back_populates="form_templates")
    parameters = relationship("Parameter", back_populates="form_template", cascade="all, delete-orphan",
                              order_by="Parameter.sort_order")
    essential_criteria = relationship("EssentialCriterion", back_populates="form_template",
                                     cascade="all, delete-orphan")
    submissions = relationship("FormSubmission", back_populates="form_template")


class Parameter(Base):
    """
    A competency/parameter within a form (e.g. 'Technical Competence').
    Has sub-parameters as children. Weight auto-normalizes to sum=100 at the form level.
    """
    __tablename__ = "parameters"

    id = Column(String(36), primary_key=True)
    form_template_id = Column(String(36), ForeignKey("form_templates.id", ondelete="CASCADE"), nullable=False)
    parent_id = Column(String(36), ForeignKey("parameters.id", ondelete="CASCADE"), nullable=True,
                       doc="NULL = top-level parameter; non-null = sub-parameter")
    code = Column(String(50), nullable=False)
    label = Column(String(500), nullable=False)
    description = Column(Text)
    weight = Column(Float, nullable=False, default=0, doc="Weight within parent group, 0-100")
    data_type = Column(String(30), nullable=False, default="RATING_1_5",
                       doc="RATING_1_5 | YES_NO | PERCENTAGE | TEXT | DROPDOWN | CALCULATED")
    options = Column(JSONB, doc="For DROPDOWN: list of options. For RATING: scale config.")
    is_mandatory = Column(Boolean, default=True)
    sort_order = Column(Integer, default=0)

    form_template = relationship("FormTemplate", back_populates="parameters")
    parent = relationship("Parameter", remote_side="Parameter.id", back_populates="children")
    children = relationship("Parameter",
                            back_populates="parent",
                            cascade="all, delete-orphan",
                            single_parent=True)

    @property
    def is_top_level(self):
        return self.parent_id is None


class EssentialCriterion(Base):
    """
    YES/NO criteria. If ANY essential = 'NO', auto-flag the evaluee for review.
    """
    __tablename__ = "essential_criteria"

    id = Column(String(36), primary_key=True)
    form_template_id = Column(String(36), ForeignKey("form_templates.id", ondelete="CASCADE"), nullable=False)
    code = Column(String(50), nullable=False)
    label = Column(String(500), nullable=False)
    sort_order = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    form_template = relationship("FormTemplate", back_populates="essential_criteria")


class FrequencyRule(Base):
    """
    Defines when a feedback form must be generated for a given role.
    e.g. 'Every Audit', 'Post 5 Audits', 'Quarterly'
    """
    __tablename__ = "frequency_rules"
    __table_args__ = (UniqueConstraint("board_id", "role_id", "form_template_id"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    board_id = Column(String(36), ForeignKey("boards.id", ondelete="CASCADE"), nullable=False)
    role_id = Column(String(50), nullable=False)
    form_template_id = Column(String(36), ForeignKey("form_templates.id", ondelete="CASCADE"), nullable=False)
    trigger_type = Column(String(30), nullable=False,
                          doc="EVERY_AUDIT | POST_N_AUDITS | QUARTERLY | ANNUALLY | ON_EVENT")
    trigger_value = Column(Integer, nullable=True, doc="N for POST_N_AUDITS")
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    board = relationship("Board", back_populates="frequency_rules")
    form_template = relationship("FormTemplate")


class Assessor(Base):
    """An assessor/evaluee in the system."""
    __tablename__ = "assessors"

    id = Column(String(36), primary_key=True)
    employee_id = Column(String(50), unique=True, nullable=False)
    name = Column(String(300), nullable=False)
    email = Column(String(300))
    phone = Column(String(30))
    board_id = Column(String(36), ForeignKey("boards.id"), nullable=False)
    role_id = Column(String(50), nullable=False)
    is_active = Column(Boolean, default=True)
    audit_count = Column(Integer, default=0)
    metadata_ = Column("metadata", JSON, default=dict)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    board = relationship("Board")


class Assessment(Base):
    """A single assessment/audit event."""
    __tablename__ = "assessments"

    id = Column(String(36), primary_key=True)
    board_id = Column(String(36), ForeignKey("boards.id"), nullable=False)
    assessment_type = Column(String(50), nullable=False,
                             doc="Initial | Surveillance | Re-assessment | Extension | Onsite")
    organization_name = Column(String(500))
    scheme = Column(String(200))
    standard_version = Column(String(200))
    assessment_date = Column(DateTime, nullable=False)
    status = Column(String(30), default="IN_PROGRESS",
                    doc="IN_PROGRESS | PENDING_FEEDBACK | SCORED | CLOSED")
    created_at = Column(DateTime, default=datetime.utcnow)

    board = relationship("Board")
    submissions = relationship("FormSubmission", back_populates="assessment")


class FormSubmission(Base):
    """A filled-in feedback form for one evaluee in one assessment."""
    __tablename__ = "form_submissions"

    id = Column(String(36), primary_key=True)
    assessment_id = Column(String(36), ForeignKey("assessments.id", ondelete="CASCADE"), nullable=True)
    form_template_id = Column(String(36), ForeignKey("form_templates.id"), nullable=False)
    evaluator_id = Column(String(36), ForeignKey("assessors.id"), nullable=True)
    evaluee_id = Column(String(36), ForeignKey("assessors.id"), nullable=True)
    status = Column(String(30), default="CREATED",
                    doc="CREATED | SENT | PENDING | SUBMITTED | COMPLETED | FLAGGED")
    responses = Column(JSONB, nullable=False, default=dict,
                       doc="{ parameter_code: value, essential_code: 'YES'|'NO' }")
    form_score = Column(Float, nullable=True, doc="Calculated after submission")
    essential_flag = Column(Boolean, default=False, doc="True if any essential='NO'")
    comments = Column(Text)
    submission_token = Column(String(36), unique=True, index=True, nullable=True,
                              doc="Public URL token for Google-Forms-style fill link")
    submitted_at = Column(DateTime)
    created_at = Column(DateTime, default=datetime.utcnow)

    assessment = relationship("Assessment", back_populates="submissions")
    form_template = relationship("FormTemplate", back_populates="submissions")
    evaluator = relationship("Assessor", foreign_keys=[evaluator_id])
    evaluee = relationship("Assessor", foreign_keys=[evaluee_id])


class AuditScore(Base):
    """Aggregated score for one evaluee across all forms in one assessment."""
    __tablename__ = "audit_scores"
    __table_args__ = (UniqueConstraint("assessment_id", "evaluee_id"),)

    id = Column(String(36), primary_key=True)
    assessment_id = Column(String(36), ForeignKey("assessments.id", ondelete="CASCADE"), nullable=False)
    evaluee_id = Column(String(36), ForeignKey("assessors.id"), nullable=False)
    board_id = Column(String(36), ForeignKey("boards.id"), nullable=False)
    form_scores = Column(JSONB, doc="{ form_code: { score, weight } }")
    final_score = Column(Float, nullable=False)
    base_100_score = Column(Float, nullable=True, doc="Normalized 0-100 score for cross-board comparison")
    star_rating = Column(Integer)
    essential_flag = Column(Boolean, default=False)
    calculated_at = Column(DateTime, default=datetime.utcnow)

    assessment = relationship("Assessment")
    evaluee = relationship("Assessor")


class CumulativeRating(Base):
    """Rolling cumulative rating for an assessor (avg of last N audits)."""
    __tablename__ = "cumulative_ratings"
    __table_args__ = (UniqueConstraint("evaluee_id", "board_id"),)

    id = Column(String(36), primary_key=True)
    evaluee_id = Column(String(36), ForeignKey("assessors.id"), nullable=False)
    board_id = Column(String(36), ForeignKey("boards.id"), nullable=False)
    window_size = Column(Integer, doc="Number of audits averaged (e.g. 5 or 10)")
    audit_scores_used = Column(JSONB, doc="List of audit_score IDs included")
    cumulative_score = Column(Float, nullable=False)
    star_rating = Column(Integer)
    has_essential_flags = Column(Boolean, default=False)
    updated_at = Column(DateTime, default=datetime.utcnow)

    evaluee = relationship("Assessor")


class Webhook(Base):
    """External integration triggers — fires on assessment events."""
    __tablename__ = "webhooks"

    id = Column(String(36), primary_key=True)
    board_id = Column(String(36), ForeignKey("boards.id", ondelete="CASCADE"), nullable=False)
    event_type = Column(String(50), nullable=False,
                        doc="ASSESSMENT_CREATED | FEEDBACK_DUE | SCORE_CALCULATED | ESSENTIAL_FLAGGED")
    target_url = Column(String(1000), nullable=False)
    secret = Column(String(200))
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    board = relationship("Board", back_populates="webhooks")


class PortalAdapter(Base):
    """
    Translation layer: maps an external portal's IDs/terminology to internal PMS concepts.
    One record per (board, portal) pair.
    e.g. portal_id='nabl-portal-v2', role_map={'101': 'ROLE_LEAD', '102': 'ROLE_PEER'},
         event_map={'assessment_done': 'ASSESSMENT_COMPLETE'}
    """
    __tablename__ = "portal_adapters"
    __table_args__ = (UniqueConstraint("board_id", "portal_id"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    board_id = Column(String(36), ForeignKey("boards.id", ondelete="CASCADE"), nullable=False)
    portal_id = Column(String(100), nullable=False, doc="External portal identifier string")
    role_map = Column(JSONB, nullable=False, default=dict,
                      doc="{ external_role_id: internal_system_role_id }")
    event_map = Column(JSONB, nullable=False, default=dict,
                       doc="{ external_event_type: internal_event_type }")
    vocabulary_map = Column(JSONB, default=dict,
                            doc="{ portal_term: pms_term } for display translation")
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    board = relationship("Board", back_populates="portal_adapters")


class AuditLog(Base):
    """
    Persists every inbound trigger, outbound dispatch, and internal config change.
    Provides full traceability for every integration and administrative event.
    direction: INBOUND | OUTBOUND | SYSTEM
    """
    __tablename__ = "audit_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    board_id = Column(String(36), ForeignKey("boards.id"), nullable=True)
    direction = Column(String(10), nullable=False, doc="INBOUND | OUTBOUND | SYSTEM")
    event_type = Column(String(50))
    portal_id = Column(String(100), nullable=True, doc="Source portal for INBOUND; target for OUTBOUND")
    assessment_id = Column(String(36), nullable=True)
    raw_payload = Column(JSONB, nullable=True)
    translated_payload = Column(JSONB, nullable=True)
    status = Column(String(20), default="received",
                    doc="received | processed | failed | dispatched")
    error = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


def log_config_change(db, board_id: str, event_type: str, entity: str, entity_id, changes: dict):
    """
    Write a SYSTEM-direction AuditLog row for any config-level mutation
    (form edit, role edit, webhook change, etc.).
    Call before db.commit() so the log is part of the same transaction.
    """
    entry = AuditLog(
        board_id=board_id,
        direction="SYSTEM",
        event_type=event_type,
        status="processed",
        raw_payload={"entity": entity, "entity_id": str(entity_id), "changes": changes},
    )
    db.add(entry)
