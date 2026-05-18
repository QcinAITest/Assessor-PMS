from datetime import datetime
from typing import Optional

import jwt
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.auth import User
from app.models.board import Board
from app.services.auth_service import (
    create_access_token, decode_token, hash_password, verify_password
)
import uuid

router = APIRouter(prefix="/api/v1/auth", tags=["Authentication"])
_security = HTTPBearer(auto_error=False)


# --------------------------------------------------------------------------- #
# Schemas                                                                      #
# --------------------------------------------------------------------------- #

class LoginRequest(BaseModel):
    email: str
    password: str


class UserCreate(BaseModel):
    email: str
    full_name: str
    password: str
    role: str = "board_admin"
    board_id: Optional[str] = None


class UserUpdate(BaseModel):
    full_name: Optional[str] = None
    password: Optional[str] = None
    is_active: Optional[bool] = None
    board_id: Optional[str] = None


# --------------------------------------------------------------------------- #
# Auth dependency                                                              #
# --------------------------------------------------------------------------- #

async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(_security),
    db: Session = Depends(get_db),
) -> User:
    if not credentials:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated")
    try:
        payload = decode_token(credentials.credentials)
        user = db.query(User).filter(User.id == payload["sub"]).first()
        if not user or not user.is_active:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "User inactive or not found")
        return user
    except jwt.ExpiredSignatureError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Token expired")
    except Exception:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid token")


async def require_system_admin(user: User = Depends(get_current_user)) -> User:
    if user.role != "super_admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "System admin access required")
    return user


async def require_board_access(
    board_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> User:
    """Board admin can only access their own board; system admin can access any."""
    if user.role == "super_admin":
        return user
    # Resolve code → ID
    board = db.query(Board).filter(
        (Board.id == board_id) | (Board.code == board_id.upper())
    ).first()
    if not board:
        raise HTTPException(404, "Board not found")
    if user.board_id != board.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Access denied for this board")
    return user


# --------------------------------------------------------------------------- #
# Endpoints                                                                    #
# --------------------------------------------------------------------------- #

@router.post("/login")
def login(data: LoginRequest, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == data.email.lower().strip()).first()
    if not user or not verify_password(data.password, user.password_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid email or password")
    if not user.is_active:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Account deactivated")

    user.last_login = datetime.utcnow()
    db.commit()

    token = create_access_token(user.id, user.email, user.role, user.board_id)
    return {
        "access_token": token,
        "token_type": "bearer",
        "user": _user_dict(user),
    }


@router.get("/me")
def get_me(current_user: User = Depends(get_current_user)):
    return _user_dict(current_user)


# --------------------------------------------------------------------------- #
# User management (system admin only)                                         #
# --------------------------------------------------------------------------- #

@router.get("/users")
def list_users(
    _: User = Depends(require_system_admin),
    db: Session = Depends(get_db),
):
    users = db.query(User).all()
    return [_user_dict(u) for u in users]


@router.post("/users", status_code=201)
def create_user(
    data: UserCreate,
    _: User = Depends(require_system_admin),
    db: Session = Depends(get_db),
):
    if db.query(User).filter(User.email == data.email.lower()).first():
        raise HTTPException(409, f"User '{data.email}' already exists")

    if data.role == "board_admin" and not data.board_id:
        raise HTTPException(400, "board_id required for board_admin role")

    user = User(
        id=str(uuid.uuid4()),
        email=data.email.lower().strip(),
        full_name=data.full_name,
        password_hash=hash_password(data.password),
        role=data.role,
        board_id=data.board_id,
    )
    db.add(user)
    db.commit()
    return _user_dict(user)


@router.put("/users/{user_id}")
def update_user(
    user_id: str,
    data: UserUpdate,
    _: User = Depends(require_system_admin),
    db: Session = Depends(get_db),
):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(404, "User not found")

    if data.full_name is not None:
        user.full_name = data.full_name
    if data.password is not None:
        user.password_hash = hash_password(data.password)
    if data.is_active is not None:
        user.is_active = data.is_active
    if data.board_id is not None:
        user.board_id = data.board_id

    db.commit()
    return _user_dict(user)


@router.delete("/users/{user_id}", status_code=204)
def delete_user(
    user_id: str,
    current_user: User = Depends(require_system_admin),
    db: Session = Depends(get_db),
):
    if user_id == current_user.id:
        raise HTTPException(400, "Cannot delete your own account")
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(404, "User not found")
    db.delete(user)
    db.commit()


def _user_dict(u: User) -> dict:
    return {
        "id": u.id,
        "email": u.email,
        "full_name": u.full_name,
        "role": u.role,
        "board_id": u.board_id,
        "is_active": u.is_active,
        "last_login": u.last_login,
        "created_at": u.created_at,
    }
