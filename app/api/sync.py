"""
Bulk sync endpoints for assessors and admin users.
Designed for push-model integration: QCI portal calls these endpoints.
"""
import csv
import io
import random
import string
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
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

def _get_board_or_404(db: Session, board_id: str) -> Board:
    board = db.query(Board).filter(
        (Board.id == board_id) | (Board.code == board_id.upper())
    ).first()
    if not board:
        raise HTTPException(404, "Board not found")
    return board


def _perform_assessor_sync(db: Session, board: Board, items: list, deactivate_missing: bool,
                           role_map: dict, portal_id=None) -> dict:
    """Shared upsert used by both the JSON sync endpoint and the file upload.
    `items` is a list of dicts: {employee_id, name, email, role_id, is_active}."""
    created = 0
    updated = 0
    errors = []
    incoming_ids = {it["employee_id"] for it in items}

    for it in items:
        try:
            resolved_role_id = role_map.get(it.get("role_id"), it.get("role_id"))
            existing = db.query(Assessor).filter(
                Assessor.employee_id == it["employee_id"],
                Assessor.board_id == board.id,
            ).first()
            if existing is None:
                db.add(Assessor(
                    id=str(uuid.uuid4()),
                    board_id=board.id,
                    employee_id=it["employee_id"],
                    name=it["name"],
                    email=it.get("email"),
                    role_id=resolved_role_id,
                    is_active=it.get("is_active", True),
                ))
                created += 1
            else:
                changed = False
                for field, value in [
                    ("name", it["name"]),
                    ("email", it.get("email")),
                    ("role_id", resolved_role_id),
                    ("is_active", it.get("is_active", True)),
                ]:
                    if getattr(existing, field) != value:
                        setattr(existing, field, value)
                        changed = True
                if changed:
                    updated += 1
        except Exception as e:
            errors.append({"employee_id": it.get("employee_id"), "error": str(e)})

    deactivated = 0
    if deactivate_missing:
        stale = db.query(Assessor).filter(
            Assessor.board_id == board.id,
            Assessor.is_active == True,
            ~Assessor.employee_id.in_(incoming_ids),
        ).all()
        for a in stale:
            a.is_active = False
            deactivated += 1

    db.add(AuditLog(
        board_id=board.id,
        direction="SYSTEM",
        event_type="ASSESSOR_SYNC_COMPLETE",
        portal_id=portal_id,
        raw_payload={"created": created, "updated": updated, "deactivated": deactivated, "errors": len(errors)},
        status="processed",
    ))
    db.commit()
    return {"created": created, "updated": updated, "deactivated": deactivated, "errors": errors}


def _rows_to_assessors(rows: list) -> list:
    """Map parsed spreadsheet rows (dicts keyed by lowercased header) to sync items.
    Requires employee_id and name; email/role_id optional."""
    out = []
    for r in rows:
        emp = str(r.get("employee_id", "") or "").strip()
        name = str(r.get("name", "") or "").strip()
        if not emp or not name:
            continue
        out.append({
            "employee_id": emp,
            "name": name,
            "email": (str(r.get("email", "") or "").strip() or None),
            "role_id": (str(r.get("role_id", "") or "").strip()),
            "is_active": True,
        })
    return out


def _parse_csv_bytes(content: bytes) -> list:
    text = content.decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(text))
    rows = [{(k or "").strip().lower(): v for k, v in row.items()} for row in reader]
    return _rows_to_assessors(rows)


def _parse_xlsx_bytes(content: bytes) -> list:
    try:
        import openpyxl
    except ImportError:
        raise HTTPException(400, "Excel support is not installed on the server. Upload a CSV instead.")
    try:
        wb = openpyxl.load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    except Exception:
        raise HTTPException(
            400,
            "Could not read the Excel file. It must be a valid .xlsx (legacy .xls and "
            "corrupt/password-protected files aren't supported — re-save as .xlsx or CSV).",
        )
    ws = wb.active  # only the active/first sheet is read
    it = ws.iter_rows(values_only=True)
    try:
        header = [str(h).strip().lower() if h is not None else "" for h in next(it)]
    except StopIteration:
        return []
    rows = []
    for values in it:
        row = {header[i]: values[i] for i in range(min(len(header), len(values)))}
        rows.append(row)
    return _rows_to_assessors(rows)


@router.post("/boards/{board_id}/assessors")
def sync_assessors(
    board_id: str,
    data: AssessorSyncRequest,
    current_user: User = Depends(require_system_admin),  # bulk upload is system-admin only
    db: Session = Depends(get_db),
):
    board = _get_board_or_404(db, board_id)
    role_map: dict = {}
    if data.portal_id:
        adapter = db.query(PortalAdapter).filter(
            PortalAdapter.board_id == board.id,
            PortalAdapter.portal_id == data.portal_id,
            PortalAdapter.is_active == True,
        ).first()
        if adapter and adapter.role_map:
            role_map = adapter.role_map
    items = [{"employee_id": i.employee_id, "name": i.name, "email": i.email,
              "role_id": i.role_id, "is_active": i.is_active} for i in data.assessors]
    return _perform_assessor_sync(db, board, items, data.deactivate_missing, role_map, data.portal_id)


@router.post("/boards/{board_id}/assessors/upload")
def upload_assessors(
    board_id: str,
    file: UploadFile = File(...),
    deactivate_missing: bool = Form(False),
    current_user: User = Depends(require_system_admin),  # system-admin only
    db: Session = Depends(get_db),
):
    """Bulk upload assessors from a CSV or Excel (.xlsx) file.
    Columns: employee_id, name, email, role_id (employee_id and name required)."""
    board = _get_board_or_404(db, board_id)
    content = file.file.read()
    fname = (file.filename or "").lower()
    if fname.endswith(".xls") and not fname.endswith(".xlsx"):
        raise HTTPException(400, "Legacy .xls files aren't supported — save the file as .xlsx or CSV and re-upload.")
    if fname.endswith(".xlsx") or fname.endswith(".xlsm"):
        items = _parse_xlsx_bytes(content)
    else:
        try:
            items = _parse_csv_bytes(content)
        except HTTPException:
            raise
        except Exception:
            raise HTTPException(400, "Could not read the file as CSV. Check it's a plain .csv or .xlsx file.")
    if not items:
        raise HTTPException(
            400,
            "No valid rows found. Ensure the first row has the column headers "
            "employee_id, name, email, role_id — and each row has an employee_id and a name.",
        )
    return _perform_assessor_sync(db, board, items, deactivate_missing, {}, portal_id=None)


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
