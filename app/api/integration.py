"""
Generic Integration Layer for QCI PMS.

Provides:
  POST /api/v1/ingest/{board_code}   — accepts any portal JSON payload, translates
                                       via PortalAdapter, routes by event type
  GET  /api/v1/assessments/{id}/status — blocking status check for external portals
  GET  /api/v1/boards/{board_id}/audit-logs — view ingestion history
  CRUD /api/v1/boards/{board_id}/portal-adapters — manage adapters via UI
"""
import logging
from datetime import datetime
from typing import Any, Dict, Optional
import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.board import (
    Assessment, AuditLog, Board, Assessor, FormSubmission,
    PortalAdapter,
)
from app.services.frequency_manager import (
    create_pending_submissions, evaluate_triggers, increment_audit_count
)

logger = logging.getLogger("qci_pms.integration")

router = APIRouter(prefix="/api/v1", tags=["Integration"])


# --------------------------------------------------------------------------- #
# Pydantic schemas                                                             #
# --------------------------------------------------------------------------- #

class PortalAdapterCreate(BaseModel):
    portal_id: str
    role_map: Dict[str, str] = {}
    event_map: Dict[str, str] = {}
    vocabulary_map: Dict[str, str] = {}
    is_active: bool = True


class PortalAdapterUpdate(BaseModel):
    role_map: Optional[Dict[str, str]] = None
    event_map: Optional[Dict[str, str]] = None
    vocabulary_map: Optional[Dict[str, str]] = None
    is_active: Optional[bool] = None


# --------------------------------------------------------------------------- #
# Helpers                                                                      #
# --------------------------------------------------------------------------- #

def _get_board(db: Session, board_code: str) -> Board:
    board = db.query(Board).filter(
        (Board.id == board_code) | (Board.code == board_code.upper())
    ).first()
    if not board:
        raise HTTPException(404, f"Board '{board_code}' not found")
    return board


def _write_audit_log(
    db: Session,
    *,
    direction: str,
    event_type: str,
    board_id: Optional[str] = None,
    portal_id: Optional[str] = None,
    assessment_id: Optional[str] = None,
    raw_payload: Optional[dict] = None,
    translated_payload: Optional[dict] = None,
    status: str = "received",
    error: Optional[str] = None,
) -> AuditLog:
    entry = AuditLog(
        board_id=board_id,
        direction=direction,
        event_type=event_type,
        portal_id=portal_id,
        assessment_id=assessment_id,
        raw_payload=raw_payload,
        translated_payload=translated_payload,
        status=status,
        error=error,
    )
    db.add(entry)
    db.flush()
    return entry


def _translate_payload(
    adapter: Optional[PortalAdapter],
    raw: dict,
) -> dict:
    """
    Apply role_map and event_map from the PortalAdapter to produce
    an internal-format payload. If no adapter, return raw unchanged.
    """
    if not adapter:
        return raw

    translated = dict(raw)

    # Translate event type
    ext_event = raw.get("event_type") or raw.get("event") or ""
    if ext_event and ext_event in (adapter.event_map or {}):
        translated["event_type"] = adapter.event_map[ext_event]

    # Translate evaluee_ids / role assignments
    role_map = adapter.role_map or {}
    if "evaluee_ids" in translated:
        # If payload passes external role IDs alongside evaluee ids, translate
        pass  # evaluee_ids are internal assessor UUIDs; role translation is below

    # Translate portal_role_id → internal_role_id in assessor lookups
    if "portal_role_id" in translated:
        ext_role = str(translated.pop("portal_role_id"))
        translated["role_id"] = role_map.get(ext_role, ext_role)

    # Translate list of assessors with role IDs
    if "assessors" in translated and isinstance(translated["assessors"], list):
        for a in translated["assessors"]:
            if "portal_role_id" in a:
                ext_role = str(a.pop("portal_role_id"))
                a["role_id"] = role_map.get(ext_role, ext_role)

    return translated


# --------------------------------------------------------------------------- #
# Generic ingestion endpoint                                                   #
# --------------------------------------------------------------------------- #

@router.post("/ingest/{board_code}", summary="Generic portal event ingestion")
async def ingest_portal_event(
    board_code: str,
    payload: Dict[str, Any],
    db: Session = Depends(get_db),
):
    """
    Single listener for all external portal events.

    Expected payload fields (all optional except event_type):
    ```json
    {
      "event_type": "ASSESSMENT_COMPLETE",   // or portal-specific e.g. "assessment_done"
      "portal_id": "nabl-portal-v2",         // identifies which adapter to use
      "assessment_id": "<uuid>",
      "evaluee_ids": ["<uuid>", ...],
      "metadata": {}
    }
    ```
    The board is identified from the URL path.
    Role IDs and event types are translated via the matching PortalAdapter.
    """
    board = _get_board(db, board_code)
    portal_id = payload.get("portal_id") or "default"

    # 1. Log inbound receipt
    log_entry = _write_audit_log(
        db,
        direction="INBOUND",
        event_type=payload.get("event_type", "UNKNOWN"),
        board_id=board.id,
        portal_id=portal_id,
        assessment_id=payload.get("assessment_id"),
        raw_payload=payload,
        status="received",
    )

    # 2. Find portal adapter
    adapter = (
        db.query(PortalAdapter)
        .filter(
            PortalAdapter.board_id == board.id,
            PortalAdapter.portal_id == portal_id,
            PortalAdapter.is_active == True,
        )
        .first()
    )

    # 3. Translate payload
    translated = _translate_payload(adapter, payload)
    log_entry.translated_payload = translated

    # 4. Route by (translated) event type
    event_type = translated.get("event_type", "").upper()

    try:
        if event_type == "ASSESSMENT_COMPLETE":
            result = await _handle_assessment_complete(db, board, translated)
        elif event_type == "SCORE_REQUEST":
            result = await _handle_score_request(db, board, translated)
        else:
            logger.warning(f"[INGEST] Unhandled event_type={event_type} for board={board_code}")
            result = {"message": f"Event '{event_type}' received but no handler registered"}

        log_entry.status = "processed"
        log_entry.event_type = event_type
        db.commit()
        return {"log_id": log_entry.id, "event_type": event_type, "result": result}

    except Exception as exc:
        log_entry.status = "failed"
        log_entry.error = str(exc)
        db.commit()
        logger.exception(f"[INGEST] Failed processing event={event_type} board={board_code}")
        raise HTTPException(500, f"Processing failed: {exc}") from exc


async def _handle_assessment_complete(db: Session, board: Board, payload: dict) -> dict:
    """
    Handles ASSESSMENT_COMPLETE: evaluates frequency rules and creates
    CREATED-status form submissions for each evaluee.
    """
    from app.services.webhook_service import fire_webhooks

    assessment_id = payload.get("assessment_id")
    evaluee_ids = payload.get("evaluee_ids", [])

    if not assessment_id:
        raise ValueError("assessment_id is required for ASSESSMENT_COMPLETE")

    assessment = db.query(Assessment).filter(Assessment.id == assessment_id).first()
    if not assessment:
        raise ValueError(f"Assessment '{assessment_id}' not found")

    results = []
    for eid in evaluee_ids:
        evaluee = db.query(Assessor).filter(Assessor.id == eid).first()
        if not evaluee:
            results.append({"evaluee_id": eid, "error": "Assessor not found"})
            continue

        increment_audit_count(db, evaluee)
        forms_needed = evaluate_triggers(db, board, assessment, evaluee)
        created = create_pending_submissions(db, assessment, evaluee, forms_needed, board=board)

        results.append({
            "evaluee_id": eid,
            "audit_count": evaluee.audit_count,
            "forms_generated": len(created),
            "form_ids": [s.id for s in created],
        })

    assessment.status = "PENDING_FEEDBACK"
    db.flush()

    # Fix 3: fire FEEDBACK_DUE webhook
    await fire_webhooks(db, board.id, "FEEDBACK_DUE", {
        "assessment_id": assessment_id,
        "evaluee_count": len([r for r in results if "error" not in r]),
    })

    return {"assessment_id": assessment_id, "evaluees": results}


async def _handle_score_request(db: Session, board: Board, payload: dict) -> dict:
    """
    Handles SCORE_REQUEST: triggers scoring calculation for an evaluee.
    Delegates to scoring engine via lazy import to avoid circular imports.
    """
    from app.services.scoring_engine import calculate_final_audit_score, calculate_cumulative_rating
    from app.services.webhook_service import fire_webhooks

    assessment_id = payload.get("assessment_id")
    evaluee_id = payload.get("evaluee_id")

    if not assessment_id or not evaluee_id:
        raise ValueError("assessment_id and evaluee_id required for SCORE_REQUEST")

    audit_score = calculate_final_audit_score(db, assessment_id, evaluee_id, board)
    calculate_cumulative_rating(db, evaluee_id, board)
    db.flush()

    # Fix 3: fire SCORE_CALCULATED webhook
    await fire_webhooks(db, board.id, "SCORE_CALCULATED", {
        "assessment_id": assessment_id,
        "evaluee_id": evaluee_id,
        "final_score": audit_score.final_score,
        "star_rating": audit_score.star_rating,
        "essential_flag": audit_score.essential_flag,
    })

    return {
        "assessment_id": assessment_id,
        "evaluee_id": evaluee_id,
        "final_score": audit_score.final_score,
        "base_100_score": audit_score.base_100_score,
        "star_rating": audit_score.star_rating,
        "essential_flag": audit_score.essential_flag,
    }


# --------------------------------------------------------------------------- #
# Status API — blocks next stages in external portals                         #
# --------------------------------------------------------------------------- #

@router.get("/assessments/{assessment_id}/status", summary="Assessment feedback status")
def get_assessment_status(assessment_id: str, db: Session = Depends(get_db)):
    """
    Returns whether feedback is still pending for an assessment.
    External portals call this before allowing the next workflow stage.

    Response:
    ```json
    {
      "assessment_id": "...",
      "workflow_state": "PENDING_FEEDBACK",
      "pending_feedback": true,
      "pending_count": 3,
      "blocked_forms": [
        {"submission_id": "...", "evaluee_id": "...", "form_code": "...", "status": "CREATED"}
      ],
      "completed_count": 2
    }
    ```
    """
    assessment = db.query(Assessment).filter(Assessment.id == assessment_id).first()
    if not assessment:
        raise HTTPException(404, "Assessment not found")

    all_submissions = (
        db.query(FormSubmission)
        .filter(FormSubmission.assessment_id == assessment_id)
        .all()
    )

    pending_states = {"CREATED", "SENT", "PENDING"}
    done_states = {"SUBMITTED", "COMPLETED", "FLAGGED"}

    blocked = []
    completed_count = 0

    for sub in all_submissions:
        if sub.status in pending_states:
            ft_code = sub.form_template.code if sub.form_template else sub.form_template_id
            blocked.append({
                "submission_id": sub.id,
                "evaluee_id": sub.evaluee_id,
                "form_code": ft_code,
                "status": sub.status,
            })
        elif sub.status in done_states:
            completed_count += 1

    return {
        "assessment_id": assessment_id,
        "workflow_state": assessment.status,
        "pending_feedback": len(blocked) > 0,
        "pending_count": len(blocked),
        "blocked_forms": blocked,
        "completed_count": completed_count,
    }


# --------------------------------------------------------------------------- #
# Workflow state transitions                                                   #
# --------------------------------------------------------------------------- #

@router.patch("/submissions/{submission_id}/status", summary="Advance submission workflow state")
def advance_submission_status(
    submission_id: str,
    new_status: str,
    db: Session = Depends(get_db),
):
    """
    Move a FormSubmission through the state machine:
    CREATED → SENT → PENDING → SUBMITTED → COMPLETED | FLAGGED

    Called by integration layer or admin to advance state after events.
    """
    valid_states = {"CREATED", "SENT", "PENDING", "SUBMITTED", "COMPLETED", "FLAGGED"}
    if new_status not in valid_states:
        raise HTTPException(400, f"Invalid status '{new_status}'. Must be one of {valid_states}")

    sub = db.query(FormSubmission).filter(FormSubmission.id == submission_id).first()
    if not sub:
        raise HTTPException(404, "Submission not found")

    old_status = sub.status
    sub.status = new_status

    if new_status == "SUBMITTED" and not sub.submitted_at:
        sub.submitted_at = datetime.utcnow()

    db.commit()
    return {"submission_id": submission_id, "old_status": old_status, "new_status": new_status}


# --------------------------------------------------------------------------- #
# Portal Adapter CRUD                                                          #
# --------------------------------------------------------------------------- #

@router.get("/boards/{board_id}/portal-adapters")
def list_portal_adapters(board_id: str, db: Session = Depends(get_db)):
    board = _get_board(db, board_id)
    adapters = db.query(PortalAdapter).filter(PortalAdapter.board_id == board.id).all()
    return [_adapter_dict(a) for a in adapters]


@router.post("/boards/{board_id}/portal-adapters", status_code=201)
def create_portal_adapter(board_id: str, data: PortalAdapterCreate, db: Session = Depends(get_db)):
    board = _get_board(db, board_id)
    existing = db.query(PortalAdapter).filter(
        PortalAdapter.board_id == board.id,
        PortalAdapter.portal_id == data.portal_id,
    ).first()
    if existing:
        raise HTTPException(409, f"Adapter for portal '{data.portal_id}' already exists")

    adapter = PortalAdapter(board_id=board.id, **data.dict())
    db.add(adapter)
    db.commit()
    db.refresh(adapter)
    return _adapter_dict(adapter)


@router.put("/boards/{board_id}/portal-adapters/{adapter_id}")
def update_portal_adapter(
    board_id: str,
    adapter_id: int,
    data: PortalAdapterUpdate,
    db: Session = Depends(get_db),
):
    board = _get_board(db, board_id)
    adapter = db.query(PortalAdapter).filter(
        PortalAdapter.id == adapter_id,
        PortalAdapter.board_id == board.id,
    ).first()
    if not adapter:
        raise HTTPException(404, "Portal adapter not found")

    for field, value in data.dict(exclude_none=True).items():
        setattr(adapter, field, value)
    db.commit()
    db.refresh(adapter)
    return _adapter_dict(adapter)


@router.delete("/boards/{board_id}/portal-adapters/{adapter_id}", status_code=204)
def delete_portal_adapter(board_id: str, adapter_id: int, db: Session = Depends(get_db)):
    board = _get_board(db, board_id)
    adapter = db.query(PortalAdapter).filter(
        PortalAdapter.id == adapter_id,
        PortalAdapter.board_id == board.id,
    ).first()
    if not adapter:
        raise HTTPException(404, "Portal adapter not found")
    db.delete(adapter)
    db.commit()


# --------------------------------------------------------------------------- #
# Audit Log viewer                                                             #
# --------------------------------------------------------------------------- #

@router.get("/boards/{board_id}/audit-logs")
def list_audit_logs(
    board_id: str,
    direction: Optional[str] = None,
    status: Optional[str] = None,
    skip: int = 0,
    limit: int = 100,
    db: Session = Depends(get_db),
):
    board = _get_board(db, board_id)
    q = db.query(AuditLog).filter(AuditLog.board_id == board.id)
    if direction:
        q = q.filter(AuditLog.direction == direction.upper())
    if status:
        q = q.filter(AuditLog.status == status.lower())
    total = q.count()
    logs = q.order_by(AuditLog.created_at.desc()).offset(skip).limit(limit).all()
    return {"total": total, "skip": skip, "limit": limit, "items": [_log_dict(l) for l in logs]}


# --------------------------------------------------------------------------- #
# Dict helpers                                                                 #
# --------------------------------------------------------------------------- #

def _adapter_dict(a: PortalAdapter) -> dict:
    return {
        "id": a.id,
        "portal_id": a.portal_id,
        "role_map": a.role_map,
        "event_map": a.event_map,
        "vocabulary_map": a.vocabulary_map,
        "is_active": a.is_active,
        "created_at": a.created_at,
    }


def _log_dict(l: AuditLog) -> dict:
    return {
        "id": l.id,
        "direction": l.direction,
        "event_type": l.event_type,
        "portal_id": l.portal_id,
        "assessment_id": l.assessment_id,
        "status": l.status,
        "error": l.error,
        "has_raw_payload": l.raw_payload is not None,
        "has_translated": l.translated_payload is not None,
        "created_at": l.created_at,
    }
