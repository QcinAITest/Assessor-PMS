"""
Raw Form Submissions API.

Allows storing, retrieving, and managing un-normalized or legacy JSON feedback forms
without requiring complex multi-table relational decomposition.
"""
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple, Union
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field, ConfigDict
from sqlalchemy.orm import Session
from sqlalchemy import or_, desc

from app.database import get_db
from app.models.board import (
    Board, BoardRole, Assessor, Assessment, FormSubmission, FormTemplate,
    AuditScore, CumulativeRating
)
from app.models.raw_submission import RawFormSubmission
from app.services.scoring_engine import (
    calculate_final_audit_score,
    calculate_cumulative_rating,
    get_star_rating,
)

router = APIRouter(prefix="/api/v1/raw-submissions", tags=["Raw Submissions"])


def _parse_datetime(val: Any) -> Optional[datetime]:
    """Parse multiple date/time string formats into datetime, or return None."""
    if not val:
        return None
    if isinstance(val, datetime):
        return val
    if not isinstance(val, str):
        return None
    
    val = val.strip()
    if not val:
        return None

    # Try ISO format
    try:
        return datetime.fromisoformat(val.replace("Z", "+00:00"))
    except Exception:
        pass

    # Common date & datetime formats
    formats = [
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d",
        "%d-%m-%Y %H:%M:%S",
        "%d-%m-%Y",
        "%d/%m/%Y %H:%M:%S",
        "%d/%m/%Y",
        "%m/%d/%Y %H:%M:%S",
        "%m/%d/%Y",
    ]
    for fmt in formats:
        try:
            return datetime.strptime(val, fmt)
        except ValueError:
            continue

    return None


# --------------------------------------------------------------------------- #
# Schemas                                                                      #
# --------------------------------------------------------------------------- #

class RawSubmissionInput(BaseModel):
    id: Optional[int] = Field(None, description="Legacy or external ID from file (optional)")
    legacy_id: Optional[int] = Field(None, description="Legacy ID alias")
    board_code: Optional[str] = Field("NABH", description="Board code (e.g. NABH, NABL, NABCB, NABET)")
    user_name: Optional[str] = Field(None, description="User / assessor / submitter name")
    role: Optional[str] = Field(None, description="Assessor or submitter role (e.g. Observer, Assessor, Principal Assessor)")
    hospital_name: Optional[str] = Field(None, description="Hospital or organization name (if applicable)")
    other_remark: Optional[str] = Field(None, description="Remarks or additional text")
    form: Optional[Any] = Field(None, description="Form data (e.g. nested 2D array of questions, ratings, comments)")
    form_data: Optional[Any] = Field(None, description="Alias for form")
    raw_payload: Optional[Any] = Field(None, description="Complete original JSON payload if needed")
    submitted_at: Optional[Any] = Field(None, description="Submission timestamp string or ISO datetime")
    is_processed: Optional[bool] = Field(False, description="Flag indicating if processed/migrated")


class RawSubmissionBulkWrapper(BaseModel):
    submissions: List[Union[RawSubmissionInput, Dict[str, Any]]]


class RawSubmissionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    legacy_id: Optional[int] = None
    board_code: Optional[str] = "NABH"
    user_name: Optional[str] = None
    role: Optional[str] = None
    hospital_name: Optional[str] = None
    other_remark: Optional[str] = None
    form_data: Any
    raw_payload: Optional[Any] = None
    submitted_at: Optional[datetime] = None
    is_processed: bool = False
    created_at: Optional[datetime] = None


class RawSubmissionListResponse(BaseModel):
    total_count: int
    page: int
    page_size: int
    total_pages: int
    items: List[RawSubmissionResponse]


class RawSubmissionUpdate(BaseModel):
    is_processed: Optional[bool] = None
    user_name: Optional[str] = None
    role: Optional[str] = None
    hospital_name: Optional[str] = None
    other_remark: Optional[str] = None
    board_code: Optional[str] = None


# --------------------------------------------------------------------------- #
# Endpoints                                                                   #
# --------------------------------------------------------------------------- #

def parse_rating_value(val: Any) -> float:
    """Coerce string ratings (e.g. 'poor', 'good', 'fair', 'verygood', 'excellent') or numbers to 1.0 - 5.0 scale."""
    if val is None:
        return 3.0
    if isinstance(val, (int, float)):
        return min(5.0, max(1.0, float(val)))
    if isinstance(val, str):
        normalized = val.strip().lower().replace(" ", "").replace("_", "").replace("-", "")
        if normalized in ("excellent", "5", "5.0"):
            return 5.0
        elif normalized in ("verygood", "4", "4.0"):
            return 4.0
        elif normalized in ("fair", "3", "3.0"):
            return 3.0
        elif normalized in ("good", "2", "2.0"):
            return 2.0
        elif normalized in ("poor", "1", "1.0"):
            return 1.0
        try:
            num = float(val)
            return min(5.0, max(1.0, num))
        except ValueError:
            pass
    return 3.0


def _score_raw_matrix(form_data: Any) -> float:
    """
    Computes score from 2D section matrix or list of question ratings.
    Level 1: Computes average for each section.
    Level 2: Averages across all sections to get the form score (1.0 to 5.0).
    """
    if not isinstance(form_data, list) or not form_data:
        return 0.0

    section_scores = []
    for section in form_data:
        if isinstance(section, list) and section:
            q_scores = [parse_rating_value(item.get("rating")) for item in section if isinstance(item, dict)]
            if q_scores:
                section_scores.append(sum(q_scores) / len(q_scores))
        elif isinstance(section, dict):
            val = parse_rating_value(section.get("rating"))
            section_scores.append(val)

    if not section_scores:
        return 0.0

    return round(sum(section_scores) / len(section_scores), 4)


def _normalize_name_to_email(name: str, domain: str = "nabh-assessor.in") -> str:
    clean = re.sub(r'^(dr|ms|mr|mrs|prof)\.?\s*', '', name.strip(), flags=re.IGNORECASE)
    clean = re.sub(r'[^a-zA-Z0-9]+', '.', clean).strip('.').lower()
    return f"{clean}@{domain}" if clean else f"assessor.{uuid.uuid4().hex[:6]}@{domain}"


def _get_or_create_assessor(db: Session, board: Board, user_name: str, role_name: Optional[str] = None) -> Assessor:
    """Finds existing assessor by name and board, or creates a new one."""
    user_name = user_name.strip()
    assessor = db.query(Assessor).filter(
        Assessor.board_id == board.id,
        Assessor.name.ilike(user_name)
    ).first()

    if assessor:
        return assessor

    # Map role or fallback to default
    system_role_id = "ROLE_PA"
    if role_name:
        role_obj = db.query(BoardRole).filter(
            BoardRole.board_id == board.id,
            (BoardRole.display_label.ilike(f"%{role_name}%")) | (BoardRole.system_role_id.ilike(f"%{role_name}%"))
        ).first()
        if role_obj:
            system_role_id = role_obj.system_role_id

    # Generate unique employee_id
    count = db.query(Assessor).filter(Assessor.board_id == board.id).count() + 1
    emp_id = f"{board.code}-{count:04d}"
    email = _normalize_name_to_email(user_name, f"{board.code.lower()}-assessor.in")

    assessor = Assessor(
        id=str(uuid.uuid4()),
        board_id=board.id,
        employee_id=emp_id,
        name=user_name,
        email=email,
        role_id=system_role_id,
        is_active=True,
        audit_count=0
    )
    db.add(assessor)
    db.flush()
    return assessor


def _ensure_board_and_template(db: Session, board_code: str = "NABH") -> Tuple[Board, FormTemplate]:
    """Ensures that the requested board and a default form template exist in the database."""
    board = db.query(Board).filter((Board.code == board_code) | (Board.id == board_code)).first()
    if not board:
        board = Board(
            id=str(uuid.uuid4()),
            code=board_code,
            name=f"{board_code} Board",
            is_active=True,
            config={
                "rating_engine": "numeric",
                "cumulative_window": 10,
                "star_bands": [
                    {"min": 4.5, "stars": 5},
                    {"min": 4.0, "stars": 4},
                    {"min": 3.5, "stars": 3},
                    {"min": 3.0, "stars": 2},
                    {"min": 0.0, "stars": 1},
                ],
            }
        )
        db.add(board)
        db.flush()

    form_template = db.query(FormTemplate).filter(FormTemplate.board_id == board.id).first()
    if not form_template:
        form_template = FormTemplate(
            id=str(uuid.uuid4()),
            board_id=board.id,
            code=f"F_{board_code}_FEEDBACK",
            name=f"{board_code} Assessor Feedback Form",
            stakeholder_weight=1.0,
            is_mandatory=True,
            is_active=True,
            version=1
        )
        db.add(form_template)
        db.flush()

    return board, form_template


def process_raw_submission_to_scorecard(db: Session, raw_sub: RawFormSubmission) -> Optional[dict]:
    """
    Processes a raw form submission into:
    1. Assessor account (if not already present)
    2. Assessment & FormSubmission records
    3. Final AuditScore (Level 3)
    4. CumulativeRating (Level 4)
    """
    if not raw_sub.user_name or not raw_sub.user_name.strip():
        return None

    board_code = raw_sub.board_code or "NABH"
    board, form_template = _ensure_board_and_template(db, board_code)

    # 1. Get or create Assessor
    assessor = _get_or_create_assessor(db, board, raw_sub.user_name, raw_sub.role)

    # 2. Compute score from 2D raw array
    form_score = _score_raw_matrix(raw_sub.form_data)

    # 4. Create Assessment
    sub_date = raw_sub.submitted_at or datetime.now(timezone.utc)
    app_id = f"RAW-{raw_sub.legacy_id or raw_sub.id or uuid.uuid4().hex[:6]}"
    
    assessment = Assessment(
        id=str(uuid.uuid4()),
        board_id=board.id,
        application_id=app_id,
        assessment_type="Surveillance",
        organization_name=raw_sub.hospital_name or "Assessment Hospital",
        assessment_date=sub_date,
        status="SCORED"
    )
    db.add(assessment)
    db.flush()

    # 5. Create FormSubmission
    submission = FormSubmission(
        id=str(uuid.uuid4()),
        assessment_id=assessment.id,
        form_template_id=form_template.id,
        evaluee_id=assessor.id,
        form_score=form_score,
        essential_flag=False,
        status="SUBMITTED",
        submitted_at=sub_date,
        responses={"raw_data": raw_sub.form_data}
    )
    db.add(submission)
    db.flush()

    # 6. Calculate Final Audit Score (Scorecard)
    audit_score = calculate_final_audit_score(db, assessment.id, assessor.id, board)

    # 7. Update Cumulative Rating and Assessor Audit Count
    audit_count = db.query(AuditScore).filter(AuditScore.evaluee_id == assessor.id).count()
    assessor.audit_count = audit_count
    cum_rating = calculate_cumulative_rating(db, assessor.id, board)

    raw_sub.is_processed = True
    db.commit()

    return {
        "assessor_id": assessor.id,
        "assessor_name": assessor.name,
        "employee_id": assessor.employee_id,
        "assessment_id": assessment.id,
        "form_score": form_score,
        "final_score": audit_score.final_score if audit_score else form_score,
        "star_rating": audit_score.star_rating if audit_score else 1,
        "cumulative_score": cum_rating.cumulative_score if cum_rating else form_score,
    }


# --------------------------------------------------------------------------- #
# Endpoints                                                                   #
# --------------------------------------------------------------------------- #

@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    summary="Save raw form submission(s) (Single or Bulk)",
    description="Accepts either a single JSON object or a JSON array of submissions. Automatically creates assessor accounts and computes scorecards.",
)
async def create_raw_submissions(
    request: Request,
    db: Session = Depends(get_db),
):
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON payload")

    items_to_process = []
    if isinstance(body, list):
        items_to_process = body
    elif isinstance(body, dict):
        if "submissions" in body and isinstance(body["submissions"], list):
            items_to_process = body["submissions"]
        else:
            items_to_process = [body]
    else:
        raise HTTPException(status_code=400, detail="Payload must be a JSON object or JSON array")

    if not items_to_process:
        raise HTTPException(status_code=400, detail="No items provided to save")

    saved_records = []
    for item in items_to_process:
        if not isinstance(item, dict):
            continue

        # Extract fields flexibly
        leg_id = item.get("legacy_id") if item.get("legacy_id") is not None else item.get("id")
        f_data = item.get("form_data") if item.get("form_data") is not None else item.get("form", [])
        u_name = item.get("user_name") or None
        r_role = item.get("role") or None
        h_name = item.get("hospital_name") or None
        o_remark = item.get("other_remark") or None
        b_code = item.get("board_code") or "NABH"
        raw_p = item.get("raw_payload") or item
        sub_at = _parse_datetime(item.get("submitted_at") or item.get("created_at") or item.get("date"))
        is_proc = bool(item.get("is_processed", False))

        record = RawFormSubmission(
            legacy_id=leg_id,
            board_code=b_code,
            user_name=u_name,
            role=r_role,
            hospital_name=h_name,
            other_remark=o_remark,
            form_data=f_data,
            raw_payload=raw_p,
            submitted_at=sub_at,
            is_processed=is_proc,
            created_at=datetime.now(timezone.utc)
        )
        saved_records.append(record)

    db.add_all(saved_records)
    db.commit()

    # Automatically process valid raw submissions into Assessors & Scorecards
    processed_results = []
    for rec in saved_records:
        if rec.user_name and not rec.is_processed:
            res = process_raw_submission_to_scorecard(db, rec)
            if res:
                processed_results.append(res)

    return {
        "status": "success",
        "message": f"Successfully created {len(saved_records)} submission(s)",
        "saved_count": len(saved_records),
        "saved_ids": [r.id for r in saved_records],
        "processed_scorecards": processed_results
    }


@router.post(
    "/process-all",
    summary="Process all unprocessed raw submissions into Assessors and Scorecards",
)
def process_all_raw_submissions(db: Session = Depends(get_db)):
    unprocessed = db.query(RawFormSubmission).filter(
        RawFormSubmission.is_processed == False,  # noqa: E712
        RawFormSubmission.user_name != None,  # noqa: E711
        RawFormSubmission.user_name != ""
    ).all()

    results = []
    for raw in unprocessed:
        try:
            res = process_raw_submission_to_scorecard(db, raw)
            if res:
                results.append(res)
        except Exception as e:
            db.rollback()
            print(f"Error processing raw submission {raw.id}: {e}")

    return {
        "status": "success",
        "total_unprocessed_found": len(unprocessed),
        "processed_count": len(results),
        "results": results
    }



@router.get(
    "",
    response_model=RawSubmissionListResponse,
    summary="Get and filter raw form submissions",
    description="Retrieve paginated raw form submissions with optional filtering on board, role, user, and status.",
)
def get_raw_submissions(
    board_code: Optional[str] = Query(None, description="Filter by board code (e.g. NABH, NABL)"),
    role: Optional[str] = Query(None, description="Filter by role (e.g. Observer, Assessor)"),
    user_name: Optional[str] = Query(None, description="Partial or exact match on user/assessor name"),
    hospital_name: Optional[str] = Query(None, description="Partial match on hospital/organization name"),
    legacy_id: Optional[int] = Query(None, description="Filter by legacy ID"),
    is_processed: Optional[bool] = Query(None, description="Filter by processed status"),
    search: Optional[str] = Query(None, description="Search across user_name, hospital_name, and other_remark"),
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(50, ge=1, le=500, description="Items per page (max 500)"),
    db: Session = Depends(get_db),
):
    query = db.query(RawFormSubmission)

    if board_code:
        query = query.filter(RawFormSubmission.board_code == board_code)
    if role:
        query = query.filter(RawFormSubmission.role == role)
    if user_name:
        query = query.filter(RawFormSubmission.user_name.ilike(f"%{user_name}%"))
    if hospital_name:
        query = query.filter(RawFormSubmission.hospital_name.ilike(f"%{hospital_name}%"))
    if legacy_id is not None:
        query = query.filter(RawFormSubmission.legacy_id == legacy_id)
    if is_processed is not None:
        query = query.filter(RawFormSubmission.is_processed == is_processed)
    if search:
        pattern = f"%{search}%"
        query = query.filter(
            or_(
                RawFormSubmission.user_name.ilike(pattern),
                RawFormSubmission.hospital_name.ilike(pattern),
                RawFormSubmission.other_remark.ilike(pattern),
            )
        )

    total_count = query.count()
    total_pages = (total_count + page_size - 1) // page_size if total_count > 0 else 1

    items = (
        query.order_by(desc(RawFormSubmission.id))
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )

    return RawSubmissionListResponse(
        total_count=total_count,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
        items=items,
    )


@router.get(
    "/{submission_id}",
    response_model=RawSubmissionResponse,
    summary="Get single raw form submission by ID",
)
def get_raw_submission_by_id(
    submission_id: int,
    db: Session = Depends(get_db),
):
    record = db.query(RawFormSubmission).filter(RawFormSubmission.id == submission_id).first()
    if not record:
        raise HTTPException(status_code=404, detail="Raw submission not found")
    return record


@router.patch(
    "/{submission_id}",
    response_model=RawSubmissionResponse,
    summary="Update raw form submission",
)
def update_raw_submission(
    submission_id: int,
    data: RawSubmissionUpdate,
    db: Session = Depends(get_db),
):
    record = db.query(RawFormSubmission).filter(RawFormSubmission.id == submission_id).first()
    if not record:
        raise HTTPException(status_code=404, detail="Raw submission not found")

    update_dict = data.model_dump(exclude_unset=True)
    for k, v in update_dict.items():
        setattr(record, k, v)

    db.commit()
    db.refresh(record)
    return record


@router.delete(
    "/{submission_id}",
    status_code=status.HTTP_200_OK,
    summary="Delete a raw submission",
)
def delete_raw_submission(
    submission_id: int,
    db: Session = Depends(get_db),
):
    record = db.query(RawFormSubmission).filter(RawFormSubmission.id == submission_id).first()
    if not record:
        raise HTTPException(status_code=404, detail="Raw submission not found")

    db.delete(record)
    db.commit()
    return {"status": "success", "message": f"Raw submission {submission_id} deleted"}
