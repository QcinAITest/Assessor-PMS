"""
Raw Form Submissions API.

Allows storing, retrieving, and managing un-normalized or legacy JSON feedback forms
without requiring complex multi-table relational decomposition.
"""
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Union
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field, ConfigDict
from sqlalchemy.orm import Session
from sqlalchemy import or_, desc

from app.database import get_db
from app.models.raw_submission import RawFormSubmission

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

@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    summary="Save raw form submission(s) (Single or Bulk)",
    description="Accepts either a single JSON object or a JSON array of submissions.",
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

    return {
        "status": "success",
        "message": "Successfully created"
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
