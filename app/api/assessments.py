from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session
from datetime import datetime
from typing import Optional
import uuid

# ── Assessment status state machine ──────────────────────────────────────────
VALID_TRANSITIONS = {
    "IN_PROGRESS":      ["PENDING_FEEDBACK"],
    "PENDING_FEEDBACK": ["SCORED"],
    "SCORED":           ["CLOSED"],
    "CLOSED":           [],
}

from app.database import get_db
from app.models.board import (
    Board, BoardRole, Assessor, Assessment, FormSubmission, FormTemplate,
    AuditScore, CumulativeRating
)
from app.models.auth import User
from app.schemas.requests import (
    AssessorCreate, AssessorUpdate, AssessmentCreate, SubmissionCreate, TriggerAssessmentComplete
)
from app.models.board import log_config_change
from app.services.scoring_engine import (
    calculate_form_score, calculate_final_audit_score, calculate_cumulative_rating, get_star_rating
)
from app.services.frequency_manager import (
    evaluate_triggers, create_pending_submissions, increment_audit_count
)
from app.api.auth import require_board_access, get_current_user

router = APIRouter(prefix="/api/v1", tags=["Assessments & Scoring"])


# --- Assessors ---
@router.get("/boards/{board_id}/assessors")
def list_assessors(
    board_id: str,
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    is_active: Optional[bool] = Query(None),
    role_id: Optional[str] = Query(None),
    _: User = Depends(require_board_access),
    db: Session = Depends(get_db),
):
    q = db.query(Assessor).filter(Assessor.board_id == board_id)
    if is_active is not None:
        q = q.filter(Assessor.is_active == is_active)
    if role_id:
        q = q.filter(Assessor.role_id == role_id)
    total = q.count()
    items = q.order_by(Assessor.name).offset(skip).limit(limit).all()
    return {"total": total, "skip": skip, "limit": limit, "items": [_assessor_dict(a) for a in items]}


@router.post("/boards/{board_id}/assessors")
def create_assessor(board_id: str, data: AssessorCreate, _: User = Depends(require_board_access), db: Session = Depends(get_db)):
    board = _get_board(db, board_id)
    # Fix 6: enforce composite uniqueness (board_id, employee_id) at API layer
    existing = db.query(Assessor).filter(
        Assessor.board_id == board.id,
        Assessor.employee_id == data.employee_id,
    ).first()
    if existing:
        raise HTTPException(409, f"Assessor with employee_id '{data.employee_id}' already exists in this board")
    assessor = Assessor(id=str(uuid.uuid4()), board_id=board.id, **data.dict())
    db.add(assessor)
    db.commit()
    return _assessor_dict(assessor)


@router.put("/boards/{board_id}/assessors/{assessor_id}")
def update_assessor(board_id: str, assessor_id: str, data: AssessorUpdate, _: User = Depends(require_board_access), db: Session = Depends(get_db)):
    assessor = db.query(Assessor).filter(Assessor.id == assessor_id, Assessor.board_id == board_id).first()
    if not assessor:
        raise HTTPException(404, "Assessor not found")
    changes = data.dict(exclude_none=True)
    for field, value in changes.items():
        setattr(assessor, field, value)
    log_config_change(db, board_id, "ASSESSOR_UPDATED", "assessor", assessor_id, changes)
    db.commit()
    return _assessor_dict(assessor)


@router.delete("/boards/{board_id}/assessors/{assessor_id}", status_code=200)
def deactivate_assessor(board_id: str, assessor_id: str, _: User = Depends(require_board_access), db: Session = Depends(get_db)):
    """Fix 8: Soft-delete — sets is_active=False. Hard delete is blocked because assessors
    are referenced in historical FormSubmissions and AuditScores."""
    assessor = db.query(Assessor).filter(Assessor.id == assessor_id, Assessor.board_id == board_id).first()
    if not assessor:
        raise HTTPException(404, "Assessor not found")
    assessor.is_active = False
    log_config_change(db, board_id, "ASSESSOR_DEACTIVATED", "assessor", assessor_id,
                      {"name": assessor.name, "employee_id": assessor.employee_id})
    db.commit()
    return {"deactivated": True, "id": assessor_id}


# --- Assessments ---
@router.get("/boards/{board_id}/assessments")
def list_assessments(
    board_id: str,
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    status: Optional[str] = Query(None),
    assessment_type: Optional[str] = Query(None),
    _: User = Depends(require_board_access),
    db: Session = Depends(get_db),
):
    q = db.query(Assessment).filter(Assessment.board_id == board_id)
    if status:
        q = q.filter(Assessment.status == status)
    if assessment_type:
        q = q.filter(Assessment.assessment_type == assessment_type)
    total = q.count()
    items = q.order_by(Assessment.assessment_date.desc()).offset(skip).limit(limit).all()
    return {"total": total, "skip": skip, "limit": limit, "items": [_assessment_dict(a) for a in items]}


@router.post("/boards/{board_id}/assessments")
def create_assessment(board_id: str, data: AssessmentCreate, _: User = Depends(require_board_access), db: Session = Depends(get_db)):
    board = _get_board(db, board_id)
    assessment = Assessment(id=str(uuid.uuid4()), board_id=board.id, **data.dict())
    db.add(assessment)
    db.commit()
    return _assessment_dict(assessment)


# --- Form Submissions ---
@router.post("/assessments/{assessment_id}/submissions")
def submit_form(assessment_id: str, data: SubmissionCreate, db: Session = Depends(get_db)):
    assessment = db.query(Assessment).filter(Assessment.id == assessment_id).first()
    if not assessment:
        raise HTTPException(404, "Assessment not found")

    ft = db.query(FormTemplate).filter(FormTemplate.id == data.form_template_id).first()
    if not ft:
        raise HTTPException(404, "Form template not found")

    form_score, essential_flag = calculate_form_score(ft, data.responses)

    submission = FormSubmission(
        id=str(uuid.uuid4()),
        assessment_id=assessment_id,
        form_template_id=data.form_template_id,
        evaluator_id=data.evaluator_id,
        evaluee_id=data.evaluee_id,
        responses=data.responses,
        form_score=form_score,
        essential_flag=essential_flag,
        comments=data.comments,
        status="SUBMITTED",
        submitted_at=datetime.utcnow(),
    )
    db.add(submission)
    db.commit()

    return {
        "id": submission.id,
        "form_score": form_score,
        "essential_flag": essential_flag,
        "status": "SUBMITTED",
    }


@router.get("/assessments/{assessment_id}/submissions")
def list_submissions(assessment_id: str, db: Session = Depends(get_db)):
    subs = db.query(FormSubmission).filter(FormSubmission.assessment_id == assessment_id).all()
    return [{
        "id": s.id, "form_template_id": s.form_template_id,
        "evaluator_id": s.evaluator_id, "evaluee_id": s.evaluee_id,
        "form_score": s.form_score, "essential_flag": s.essential_flag,
        "status": s.status, "submitted_at": s.submitted_at,
    } for s in subs]


# --- Scoring ---
@router.post("/assessments/{assessment_id}/calculate-score")
async def calculate_audit_score(assessment_id: str, evaluee_id: str, db: Session = Depends(get_db)):
    from app.services.webhook_service import fire_webhooks
    assessment = db.query(Assessment).filter(Assessment.id == assessment_id).first()
    if not assessment:
        raise HTTPException(404, "Assessment not found")
    board = db.query(Board).filter(Board.id == assessment.board_id).first()

    audit_score = calculate_final_audit_score(db, assessment_id, evaluee_id, board)
    db.commit()

    # Fix 5: transition assessment to SCORED (idempotent — only from PENDING_FEEDBACK)
    if assessment.status == "PENDING_FEEDBACK":
        assessment.status = "SCORED"
        db.commit()

    # Fix 3: fire SCORE_CALCULATED webhook
    await fire_webhooks(db, board.id, "SCORE_CALCULATED", {
        "assessment_id": assessment_id,
        "evaluee_id": evaluee_id,
        "final_score": audit_score.final_score,
        "star_rating": audit_score.star_rating,
        "essential_flag": audit_score.essential_flag,
    })
    db.commit()

    # Fix 3: fire ESSENTIAL_FLAGGED webhook if needed
    if audit_score.essential_flag:
        await fire_webhooks(db, board.id, "ESSENTIAL_FLAGGED", {
            "assessment_id": assessment_id,
            "evaluee_id": evaluee_id,
        })
        db.commit()

    return {
        "audit_score_id": audit_score.id,
        "final_score": audit_score.final_score,
        "star_rating": audit_score.star_rating,
        "essential_flag": audit_score.essential_flag,
        "form_scores": audit_score.form_scores,
        "assessment_status": assessment.status,
    }


@router.get("/assessors/{assessor_id}")
def get_assessor_detail(assessor_id: str, db: Session = Depends(get_db)):
    """Return assessor profile with board + role label — used by performance card."""
    assessor = db.query(Assessor).filter(Assessor.id == assessor_id).first()
    if not assessor:
        raise HTTPException(404, "Assessor not found")
    board = db.query(Board).filter(Board.id == assessor.board_id).first()
    role_label = assessor.role_id
    if board:
        role_obj = db.query(BoardRole).filter(
            BoardRole.board_id == board.id,
            BoardRole.system_role_id == assessor.role_id,
        ).first()
        if role_obj:
            role_label = role_obj.display_label
    return {
        **_assessor_dict(assessor),
        "board_code": board.code if board else None,
        "board_name": board.name if board else None,
        "rating_engine": board.rating_engine if board else "numeric",
        "role_label": role_label,
    }


@router.get("/assessors/{assessor_id}/cumulative-rating")
def get_cumulative_rating(assessor_id: str, db: Session = Depends(get_db)):
    assessor = db.query(Assessor).filter(Assessor.id == assessor_id).first()
    if not assessor:
        raise HTTPException(404, "Assessor not found")
    board = db.query(Board).filter(Board.id == assessor.board_id).first()

    cr = calculate_cumulative_rating(db, assessor_id, board)
    db.commit()

    if not cr:
        return {"message": "No audit scores found for this assessor"}

    return {
        "cumulative_score": cr.cumulative_score,
        "star_rating": cr.star_rating,
        "window_size": cr.window_size,
        "has_essential_flags": cr.has_essential_flags,
    }


@router.get("/assessors/{assessor_id}/score-history")
def get_score_history(
    assessor_id: str,
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
):
    """Return audit scores enriched with assessment context for the performance card."""
    q = db.query(AuditScore).filter(AuditScore.evaluee_id == assessor_id)
    total = q.count()
    scores = q.order_by(AuditScore.calculated_at.asc()).offset(skip).limit(limit).all()
    result = []
    for s in scores:
        assessment = db.query(Assessment).filter(Assessment.id == s.assessment_id).first()
        result.append({
            "id": s.id,
            "assessment_id": s.assessment_id,
            "final_score": s.final_score,
            "base_100_score": s.base_100_score,
            "star_rating": s.star_rating,
            "essential_flag": s.essential_flag,
            "form_scores": s.form_scores,
            "calculated_at": s.calculated_at,
            "organization_name": assessment.organization_name if assessment else None,
            "assessment_type": assessment.assessment_type if assessment else None,
            "assessment_date": str(assessment.assessment_date)[:10] if assessment and assessment.assessment_date else None,
        })
    return {"total": total, "skip": skip, "limit": limit, "items": result}


# --- Integration Trigger ---
@router.post("/triggers/assessment-complete")
def trigger_assessment_complete(data: TriggerAssessmentComplete, db: Session = Depends(get_db)):
    """
    External assessment portals call this endpoint after an assessment is completed.
    Evaluates frequency rules and generates pending feedback forms.
    """
    assessment = db.query(Assessment).filter(Assessment.id == data.assessment_id).first()
    if not assessment:
        raise HTTPException(404, "Assessment not found")
    board = db.query(Board).filter(Board.id == assessment.board_id).first()

    results = []
    for eid in data.evaluee_ids:
        evaluee = db.query(Assessor).filter(Assessor.id == eid).first()
        if not evaluee:
            results.append({"evaluee_id": eid, "error": "Not found"})
            continue

        increment_audit_count(db, evaluee)
        forms_needed = evaluate_triggers(db, board, assessment, evaluee)
        created = create_pending_submissions(db, assessment, evaluee, forms_needed)

        results.append({
            "evaluee_id": eid,
            "audit_count": evaluee.audit_count,
            "forms_generated": len(created),
            "details": [{"form_template_id": f["form_template_id"], "reason": f["reason"]}
                        for f in forms_needed],
        })

    assessment.status = "PENDING_FEEDBACK"
    db.commit()

    return {"assessment_id": data.assessment_id, "evaluees": results}


# --- Assessment Status Transition (Fix 5) ---
class StatusTransitionBody(BaseModel):
    new_status: str


@router.patch("/boards/{board_id}/assessments/{assessment_id}/status")
def transition_assessment_status(
    board_id: str,
    assessment_id: str,
    body: StatusTransitionBody,
    _: User = Depends(require_board_access),
    db: Session = Depends(get_db),
):
    """
    Manually advance an assessment's status through the state machine.
    Valid transitions: IN_PROGRESS→PENDING_FEEDBACK, PENDING_FEEDBACK→SCORED, SCORED→CLOSED.
    PENDING_FEEDBACK→SCORED is normally automatic via calculate-score; this endpoint handles CLOSED.
    """
    assessment = db.query(Assessment).filter(
        Assessment.id == assessment_id,
        Assessment.board_id == board_id,
    ).first()
    if not assessment:
        raise HTTPException(404, "Assessment not found")

    allowed = VALID_TRANSITIONS.get(assessment.status, [])
    if body.new_status not in allowed:
        raise HTTPException(
            422,
            f"Cannot transition from '{assessment.status}' to '{body.new_status}'. "
            f"Allowed next states: {allowed or ['none (terminal state)']}",
        )

    old_status = assessment.status
    assessment.status = body.new_status
    db.commit()
    return {
        "assessment_id": assessment_id,
        "old_status": old_status,
        "new_status": assessment.status,
    }


# --- Helpers ---
def _get_board(db, board_id):
    board = db.query(Board).filter((Board.id == board_id) | (Board.code == board_id)).first()
    if not board:
        raise HTTPException(404, f"Board '{board_id}' not found")
    return board


def _assessor_dict(a):
    return {
        "id": a.id, "employee_id": a.employee_id, "name": a.name,
        "email": a.email, "role_id": a.role_id, "audit_count": a.audit_count,
        "is_active": a.is_active,
    }


def _assessment_dict(a):
    return {
        "id": a.id, "assessment_type": a.assessment_type,
        "organization_name": a.organization_name, "scheme": a.scheme,
        "assessment_date": a.assessment_date, "status": a.status,
    }
