"""
Bulk sync endpoints for assessors and admin users.
Designed for push-model integration: QCI portal calls these endpoints.
"""
import random
import string
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.auth import User
from app.models.board import Assessor, AuditLog, Board, PortalAdapter
from app.schemas.requests import AssessorSyncRequest, UserSyncRequest
from app.api.auth import get_current_user, require_board_access, require_system_admin
from app.services.auth_service import hash_password

router = APIRouter(prefix="/api/v1/sync", tags=["Sync"])


def _gen_temp_password() -> str:
    chars = string.ascii_letters + string.digits
    return "Temp@" + "".join(random.choices(chars, k=8))


# --------------------------------------------------------------------------- #
# Assessor bulk sync                                                           #
# --------------------------------------------------------------------------- #

@router.post("/boards/{board_id}/assessors")
def sync_assessors(
    board_id: str,
    data: AssessorSyncRequest,
    current_user: User = Depends(require_board_access),
    db: Session = Depends(get_db),
):
    board = db.query(Board).filter(
        (Board.id == board_id) | (Board.code == board_id.upper())
    ).first()
    if not board:
        raise HTTPException(404, "Board not found")

    # Resolve portal role_map if portal_id provided
    role_map: dict = {}
    if data.portal_id:
        adapter = db.query(PortalAdapter).filter(
            PortalAdapter.board_id == board.id,
            PortalAdapter.portal_id == data.portal_id,
            PortalAdapter.is_active == True,
        ).first()
        if adapter and adapter.role_map:
            role_map = adapter.role_map

    created = 0
    updated = 0
    errors = []
    incoming_ids = {item.employee_id for item in data.assessors}

    for item in data.assessors:
        try:
            # Translate role_id via adapter map if available
            resolved_role_id = role_map.get(item.role_id, item.role_id)

            existing = db.query(Assessor).filter(
                Assessor.employee_id == item.employee_id,
                Assessor.board_id == board.id,
            ).first()

            if existing is None:
                assessor = Assessor(
                    id=str(uuid.uuid4()),
                    board_id=board.id,
                    employee_id=item.employee_id,
                    name=item.name,
                    email=item.email,
                    role_id=resolved_role_id,
                    is_active=item.is_active,
                )
                db.add(assessor)
                created += 1
            else:
                changed = False
                for field, value in [
                    ("name", item.name),
                    ("email", item.email),
                    ("role_id", resolved_role_id),
                    ("is_active", item.is_active),
                ]:
                    if getattr(existing, field) != value:
                        setattr(existing, field, value)
                        changed = True
                if changed:
                    updated += 1
        except Exception as e:
            errors.append({"employee_id": item.employee_id, "error": str(e)})

    deactivated = 0
    if data.deactivate_missing:
        stale = db.query(Assessor).filter(
            Assessor.board_id == board.id,
            Assessor.is_active == True,
            ~Assessor.employee_id.in_(incoming_ids),
        ).all()
        for a in stale:
            a.is_active = False
            deactivated += 1

    # Audit log
    db.add(AuditLog(
        board_id=board.id,
        direction="SYSTEM",
        event_type="ASSESSOR_SYNC_COMPLETE",
        portal_id=data.portal_id,
        raw_payload={"created": created, "updated": updated, "deactivated": deactivated, "errors": len(errors)},
        status="processed",
    ))

    db.commit()
    return {"created": created, "updated": updated, "deactivated": deactivated, "errors": errors}


# --------------------------------------------------------------------------- #
# Admin user bulk sync                                                         #
# --------------------------------------------------------------------------- #

@router.post("/users")
def sync_users(
    data: UserSyncRequest,
    _: User = Depends(require_system_admin),
    db: Session = Depends(get_db),
):
    created = 0
    updated = 0
    errors = []
    new_credentials = []

    for item in data.users:
        try:
            email = item.email.lower().strip()

            # Resolve board_code → board_id
            board_id = None
            if item.board_code:
                board = db.query(Board).filter(
                    Board.code == item.board_code.upper()
                ).first()
                if not board:
                    errors.append({"email": email, "error": f"Board '{item.board_code}' not found"})
                    continue
                board_id = board.id

            existing = db.query(User).filter(User.email == email).first()

            if existing is None:
                temp_pw = _gen_temp_password()
                user = User(
                    id=str(uuid.uuid4()),
                    email=email,
                    full_name=item.full_name,
                    password_hash=hash_password(temp_pw),
                    role=item.role,
                    board_id=board_id,
                    external_id=item.external_id,
                    is_active=True,
                    created_at=datetime.utcnow(),
                )
                db.add(user)
                created += 1
                new_credentials.append({"email": email, "temp_password": temp_pw})
            else:
                # Never overwrite password
                existing.full_name = item.full_name
                existing.board_id = board_id
                existing.is_active = True
                if item.external_id is not None:
                    existing.external_id = item.external_id
                updated += 1

        except Exception as e:
            errors.append({"email": item.email, "error": str(e)})

    db.commit()
    return {
        "created": created,
        "updated": updated,
        "errors": errors,
        "new_credentials": new_credentials,
    }
