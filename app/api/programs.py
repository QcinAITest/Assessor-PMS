"""
Service Lines and Programs API.

Hierarchy: Board → ServiceLine → Program

Board admins manage service lines and programs under their board.
"""
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.board import Board
from app.models.program import ServiceLine, Program
from app.api.auth import get_current_user
from app.models.auth import User

router = APIRouter(prefix="/api/v1/boards/{board_id}", tags=["Programs"])


# --------------------------------------------------------------------------- #
# Schemas                                                                      #
# --------------------------------------------------------------------------- #

class ServiceLineCreate(BaseModel):
    code: str
    name: str
    description: Optional[str] = None
    sort_order: int = 0


class ProgramCreate(BaseModel):
    code: str
    name: str
    description: Optional[str] = None
    standard_version: Optional[str] = None
    sort_order: int = 0


# --------------------------------------------------------------------------- #
# Helpers                                                                      #
# --------------------------------------------------------------------------- #

def _get_board(db: Session, board_id: str) -> Board:
    board = db.query(Board).filter(
        (Board.id == board_id) | (Board.code == board_id.upper())
    ).first()
    if not board:
        raise HTTPException(404, f"Board '{board_id}' not found")
    return board


def _check_board_access(user: User, board: Board):
    if user.role == "SYSTEM_ADMIN":
        return
    if user.board_id != board.id:
        raise HTTPException(403, "Access denied for this board")


# --------------------------------------------------------------------------- #
# Service Lines                                                                #
# --------------------------------------------------------------------------- #

@router.get("/service-lines")
def list_service_lines(
    board_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    board = _get_board(db, board_id)
    _check_board_access(current_user, board)
    sls = (db.query(ServiceLine)
           .filter(ServiceLine.board_id == board.id)
           .order_by(ServiceLine.sort_order)
           .all())
    return [_sl_dict(sl) for sl in sls]


@router.post("/service-lines", status_code=201)
def create_service_line(
    board_id: str,
    data: ServiceLineCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    board = _get_board(db, board_id)
    _check_board_access(current_user, board)
    sl = ServiceLine(id=str(uuid.uuid4()), board_id=board.id, **data.dict())
    db.add(sl)
    db.commit()
    db.refresh(sl)
    return _sl_dict(sl)


@router.delete("/service-lines/{sl_id}", status_code=204)
def delete_service_line(
    board_id: str,
    sl_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    board = _get_board(db, board_id)
    _check_board_access(current_user, board)
    sl = db.query(ServiceLine).filter(
        ServiceLine.id == sl_id, ServiceLine.board_id == board.id
    ).first()
    if not sl:
        raise HTTPException(404, "Service line not found")
    db.delete(sl)
    db.commit()


# --------------------------------------------------------------------------- #
# Programs                                                                     #
# --------------------------------------------------------------------------- #

@router.get("/service-lines/{sl_id}/programs")
def list_programs(
    board_id: str,
    sl_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    board = _get_board(db, board_id)
    _check_board_access(current_user, board)
    programs = (db.query(Program)
                .filter(Program.service_line_id == sl_id, Program.board_id == board.id)
                .order_by(Program.sort_order)
                .all())
    return [_program_dict(p) for p in programs]


@router.post("/service-lines/{sl_id}/programs", status_code=201)
def create_program(
    board_id: str,
    sl_id: str,
    data: ProgramCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    board = _get_board(db, board_id)
    _check_board_access(current_user, board)
    sl = db.query(ServiceLine).filter(
        ServiceLine.id == sl_id, ServiceLine.board_id == board.id
    ).first()
    if not sl:
        raise HTTPException(404, "Service line not found")
    program = Program(
        id=str(uuid.uuid4()),
        service_line_id=sl_id,
        board_id=board.id,
        **data.dict(),
    )
    db.add(program)
    db.commit()
    db.refresh(program)
    return _program_dict(program)


@router.delete("/service-lines/{sl_id}/programs/{program_id}", status_code=204)
def delete_program(
    board_id: str,
    sl_id: str,
    program_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    board = _get_board(db, board_id)
    _check_board_access(current_user, board)
    prog = db.query(Program).filter(
        Program.id == program_id,
        Program.service_line_id == sl_id,
        Program.board_id == board.id,
    ).first()
    if not prog:
        raise HTTPException(404, "Program not found")
    db.delete(prog)
    db.commit()


# --------------------------------------------------------------------------- #
# Dict helpers                                                                 #
# --------------------------------------------------------------------------- #

def _sl_dict(sl: ServiceLine) -> dict:
    return {
        "id": sl.id,
        "code": sl.code,
        "name": sl.name,
        "description": sl.description,
        "sort_order": sl.sort_order,
        "is_active": sl.is_active,
        "programs": [_program_dict(p) for p in (sl.programs or [])],
    }


def _program_dict(p: Program) -> dict:
    return {
        "id": p.id,
        "code": p.code,
        "name": p.name,
        "description": p.description,
        "standard_version": p.standard_version,
        "sort_order": p.sort_order,
        "is_active": p.is_active,
    }


# --------------------------------------------------------------------------- #
# Public form submission (no auth — token-gated)                              #
# --------------------------------------------------------------------------- #

from app.models.board import FormSubmission, FormTemplate
from app.services.scoring_engine import calculate_form_score
from datetime import datetime as _dt
from fastapi import APIRouter as _AR

public_router = _AR(prefix="/api/v1/forms", tags=["Public Forms"])


@public_router.get("/{token}")
def get_public_form(token: str, db: Session = Depends(get_db)):
    """Return form structure needed to render the public fill page."""
    sub = db.query(FormSubmission).filter(FormSubmission.submission_token == token).first()
    if not sub:
        raise HTTPException(404, "Form link not found or expired")
    if sub.status in ("SUBMITTED", "COMPLETED", "FLAGGED"):
        return {"already_submitted": True, "status": sub.status}

    ft = sub.form_template
    evaluee = sub.evaluee
    assessment = sub.assessment

    return {
        "already_submitted": False,
        "submission_id": sub.id,
        "form_name": ft.name,
        "form_code": ft.code,
        "evaluee_name": evaluee.name if evaluee else "—",
        "evaluee_role": evaluee.role_id if evaluee else "—",
        "assessment_type": assessment.assessment_type if assessment else "—",
        "organization_name": assessment.organization_name if assessment else "—",
        "parameters": _format_params(ft.parameters),
        "essential_criteria": [
            {"code": ec.code, "label": ec.label} for ec in ft.essential_criteria
        ],
    }


@public_router.post("/{token}/submit")
def submit_public_form(
    token: str,
    body: dict,
    db: Session = Depends(get_db),
):
    """
    body: { "responses": { param_code: value, ... }, "comments": "..." }
    """
    sub = db.query(FormSubmission).filter(FormSubmission.submission_token == token).first()
    if not sub:
        raise HTTPException(404, "Form link not found or expired")
    if sub.status in ("SUBMITTED", "COMPLETED", "FLAGGED"):
        raise HTTPException(409, "Form already submitted")

    responses = body.get("responses", {})
    comments = body.get("comments", "")
    ft = sub.form_template

    form_score, essential_flag = calculate_form_score(ft, responses)

    sub.responses = responses
    sub.comments = comments
    sub.form_score = form_score
    sub.essential_flag = essential_flag
    sub.status = "FLAGGED" if essential_flag else "SUBMITTED"
    sub.submitted_at = _dt.utcnow()
    db.commit()

    return {
        "message": "Thank you. Your feedback has been submitted.",
        "form_score": form_score,
        "essential_flag": essential_flag,
    }


def _format_params(parameters) -> list:
    top = [p for p in parameters if p.parent_id is None]
    result = []
    for p in sorted(top, key=lambda x: x.sort_order):
        result.append({
            "id": p.id,
            "code": p.code,
            "label": p.label,
            "data_type": p.data_type,
            "weight": p.weight,
            "is_mandatory": p.is_mandatory,
            "sub_parameters": [
                {
                    "id": c.id,
                    "code": c.code,
                    "label": c.label,
                    "data_type": c.data_type,
                    "is_mandatory": c.is_mandatory,
                    "options": c.options,
                }
                for c in sorted(p.children or [], key=lambda x: x.sort_order)
            ],
        })
    return result
