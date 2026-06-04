"""
Role-Frequency Manager: determines when mandatory feedback forms should be generated.

Checks the assessor's Role + AuditCount against FrequencyRules and fires
form-generation triggers accordingly.

Integration point: external assessment portals call POST /api/v1/triggers/assessment-complete
which invokes `evaluate_triggers()` to generate pending feedback forms.
"""
from datetime import datetime, timedelta
from typing import List, Optional
from sqlalchemy.orm import Session
from app.models.board import (
    AuditLog, Board, FrequencyRule, Assessor, Assessment, FormSubmission, FormTemplate,
    Parameter, EssentialCriterion,
)
import uuid


def evaluate_triggers(
    db: Session,
    board: Board,
    assessment: Assessment,
    evaluee: Assessor,
) -> List[dict]:
    """
    Given a completed assessment, determine which feedback forms must be generated
    for the given evaluee based on the board's frequency rules.

    Returns list of { form_template_id, reason } for forms that should be created.
    """
    rules = (
        db.query(FrequencyRule)
        .filter(
            FrequencyRule.board_id == board.id,
            FrequencyRule.role_id == evaluee.role_id,
            FrequencyRule.is_active == True,
        )
        .all()
    )

    forms_to_generate = []

    for rule in rules:
        should_generate = False
        reason = ""

        if rule.trigger_type == "EVERY_AUDIT":
            should_generate = True
            reason = "Triggered on every audit"

        elif rule.trigger_type == "POST_N_AUDITS":
            n = rule.trigger_value or 5
            if evaluee.audit_count > 0 and evaluee.audit_count % n == 0:
                should_generate = True
                reason = f"Triggered after every {n} audits (current: {evaluee.audit_count})"

        elif rule.trigger_type == "QUARTERLY":
            last_submission = (
                db.query(FormSubmission)
                .filter(
                    FormSubmission.evaluee_id == evaluee.id,
                    FormSubmission.form_template_id == rule.form_template_id,
                    FormSubmission.status == "SUBMITTED",
                )
                .order_by(FormSubmission.submitted_at.desc())
                .first()
            )
            if last_submission is None or (
                datetime.utcnow() - last_submission.submitted_at > timedelta(days=90)
            ):
                should_generate = True
                reason = "Quarterly review due"

        elif rule.trigger_type == "ANNUALLY":
            last_submission = (
                db.query(FormSubmission)
                .filter(
                    FormSubmission.evaluee_id == evaluee.id,
                    FormSubmission.form_template_id == rule.form_template_id,
                    FormSubmission.status == "SUBMITTED",
                )
                .order_by(FormSubmission.submitted_at.desc())
                .first()
            )
            if last_submission is None or (
                datetime.utcnow() - last_submission.submitted_at > timedelta(days=365)
            ):
                should_generate = True
                reason = "Annual review due"

        elif rule.trigger_type == "ON_EVENT":
            should_generate = True
            reason = f"Event-triggered: {assessment.assessment_type}"

        if should_generate:
            already_exists = (
                db.query(FormSubmission)
                .filter(
                    FormSubmission.assessment_id == assessment.id,
                    FormSubmission.evaluee_id == evaluee.id,
                    FormSubmission.form_template_id == rule.form_template_id,
                )
                .first()
            )
            if not already_exists:
                forms_to_generate.append({
                    "form_template_id": rule.form_template_id,
                    "reason": reason,
                    "rule_id": rule.id,
                })

    return forms_to_generate


def _snapshot_form_template(ft: FormTemplate) -> dict:
    """
    Serialize a FormTemplate ORM object to a plain dict for archival.
    Stored on FormSubmission.form_snapshot at creation time so that
    scoring uses the original structure even if the template is later edited.
    """
    def _param_dict(p: Parameter) -> dict:
        return {
            "id": p.id,
            "code": p.code,
            "label": p.label,
            "weight": p.weight,
            "data_type": p.data_type,
            "options": p.options,
            "is_mandatory": p.is_mandatory,
            "parent_id": p.parent_id,
            "sort_order": p.sort_order,
        }

    return {
        "id": ft.id,
        "code": ft.code,
        "name": ft.name,
        "version": ft.version,
        "stakeholder_weight": ft.stakeholder_weight,
        "parameters": [_param_dict(p) for p in ft.parameters],
        "essential_criteria": [
            {"id": e.id, "code": e.code, "label": e.label}
            for e in ft.essential_criteria
        ],
    }


def create_pending_submissions(
    db: Session,
    assessment: Assessment,
    evaluee: Assessor,
    forms_to_generate: List[dict],
    board: Optional[Board] = None,
) -> List[FormSubmission]:
    """Create CREATED-status form submissions for each triggered form.

    Args:
        board: Optional Board object. When provided, token_expiry_days from its
               config is used to set token_expires_at. Defaults to 30 days.
    """
    expiry_days = 30
    if board is not None:
        expiry_days = (board.config or {}).get("token_expiry_days", 30)
    token_expires_at = datetime.utcnow() + timedelta(days=expiry_days)

    created = []
    for item in forms_to_generate:
        ft = db.query(FormTemplate).filter(
            FormTemplate.id == item["form_template_id"]
        ).first()
        snapshot = _snapshot_form_template(ft) if ft else None

        submission = FormSubmission(
            id=str(uuid.uuid4()),
            assessment_id=assessment.id,
            form_template_id=item["form_template_id"],
            evaluator_id=evaluee.id,
            evaluee_id=evaluee.id,
            status="CREATED",
            responses={},
            submission_token=str(uuid.uuid4()),
            token_expires_at=token_expires_at,
            form_snapshot=snapshot,
        )
        db.add(submission)
        created.append(submission)

    if created:
        log_entry = AuditLog(
            board_id=assessment.board_id,
            direction="INBOUND",
            event_type="FORMS_DISPATCHED",
            assessment_id=assessment.id,
            raw_payload={
                "evaluee_id": evaluee.id,
                "forms_created": [s.id for s in created],
                "reasons": [f["reason"] for f in forms_to_generate],
            },
            status="processed",
        )
        db.add(log_entry)

    db.flush()
    return created


def increment_audit_count(db: Session, evaluee: Assessor):
    """Increment the assessor's audit counter after each completed assessment."""
    evaluee.audit_count = (evaluee.audit_count or 0) + 1
    db.flush()
