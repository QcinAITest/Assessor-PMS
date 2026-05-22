"""
Shared test fixtures for QCI PMS.

Uses an in-memory SQLite database so tests are isolated from the
production qci_pms.db file and each test run starts clean.
"""
import os
import sys
import uuid
import pytest

# ---------------------------------------------------------------------------
# 1. Force in-memory SQLite BEFORE any app modules are imported so that
#    app/database.py picks up the env var on first load.
# ---------------------------------------------------------------------------
os.environ["DATABASE_URL"] = "sqlite:///:memory:"
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

# ---------------------------------------------------------------------------
# 2. Patch PostgreSQL JSONB → generic JSON so that SQLite can create tables.
#    Must happen before any app.models are imported.
# ---------------------------------------------------------------------------
from sqlalchemy.types import JSON as _JSON  # noqa: E402
import sqlalchemy.dialects.postgresql as _pg  # noqa: E402
_pg.JSONB = _JSON  # type: ignore[attr-defined]
# Also patch the module-level symbol that board.py imports directly
import sys as _sys  # noqa: E402
if "app.models.board" in _sys.modules:
    import importlib
    importlib.reload(_sys.modules["app.models.board"])

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from app.database import Base, get_db
from app.models.board import Board, BoardRole, Assessor, Assessment, FormTemplate, Parameter, EssentialCriterion
from app.models.auth import User
from app.services.auth_service import hash_password, create_access_token
from main import app

# ---------------------------------------------------------------------------
# Shared in-memory SQLite.
# Using "file::memory:?cache=shared" so that ALL connections within the
# same process see the same data — crucial for pytest fixtures vs
# FastAPI's dependency-injected sessions.
# ---------------------------------------------------------------------------
TEST_DB_URL = "sqlite:///file::memory:?cache=shared&uri=true"
test_engine = create_engine(
    TEST_DB_URL,
    connect_args={"check_same_thread": False, "uri": True},
)
TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)


@pytest.fixture(scope="session", autouse=True)
def create_tables():
    """Create all tables once per test session."""
    Base.metadata.create_all(bind=test_engine)
    yield
    Base.metadata.drop_all(bind=test_engine)


@pytest.fixture(autouse=True)
def clean_db(create_tables):  # explicit dep ensures tables exist before clean runs
    """Wipe all rows before every test so each test starts with a clean slate."""
    yield
    with test_engine.connect() as conn:
        for table in reversed(Base.metadata.sorted_tables):
            try:
                conn.execute(table.delete())
            except Exception:
                pass  # Skip tables that don't exist in SQLite (e.g. PG-only system tables)
        conn.commit()


@pytest.fixture
def db():
    """SQLAlchemy session backed by the in-memory test DB."""
    session = TestSessionLocal()
    try:
        yield session
    finally:
        session.close()


def _override_get_db():
    session = TestSessionLocal()
    try:
        yield session
    finally:
        session.close()


# Override the FastAPI dependency so requests hit the same in-memory DB.
app.dependency_overrides[get_db] = _override_get_db


@pytest.fixture
def client():
    """Unauthenticated test client."""
    return TestClient(app)


# ---------------------------------------------------------------------------
# Helper: seed a minimal board
# ---------------------------------------------------------------------------
def make_board(db, code="NABL", rating_engine="numeric") -> Board:
    """Create and persist a minimal board with one role and one form."""
    board = Board(
        id=str(uuid.uuid4()),
        code=code,
        name=f"{code} Test Board",
        description="Test board",
        is_active=True,
        config={
            "rating_engine": rating_engine,
            "cumulative_window": 3,
            "star_bands": [
                {"min": 4.5, "max": 5.0, "stars": 5},
                {"min": 4.0, "max": 4.49, "stars": 4},
                {"min": 3.5, "max": 3.99, "stars": 3},
                {"min": 3.0, "max": 3.49, "stars": 2},
                {"min": 0.0, "max": 2.99, "stars": 1},
            ],
        },
    )
    role = BoardRole(
        board_id=board.id,
        system_role_id="ROLE_LEAD",
        display_label="Lead Assessor",
        can_be_evaluator=True,
        can_be_evaluee=True,
    )
    db.add_all([board, role])

    form = FormTemplate(
        id=str(uuid.uuid4()),
        board_id=board.id,
        code="F_TEST",
        name="Test Form",
        stakeholder_weight=1.0,
        is_mandatory=True,
        is_active=True,
        version=1,
    )
    db.add(form)

    # Top-level CALCULATED parameter
    top = Parameter(
        id=str(uuid.uuid4()),
        form_template_id=form.id,
        code="C1",
        label="Competency 1",
        weight=100.0,
        data_type="CALCULATED",
        parent_id=None,
        sort_order=1,
    )
    db.add(top)

    # Sub-parameter
    sub = Parameter(
        id=str(uuid.uuid4()),
        form_template_id=form.id,
        code="C1_S1",
        label="Sub 1",
        weight=0,
        data_type="RATING_1_5",
        parent_id=top.id,
        sort_order=1,
    )
    db.add(sub)

    # Essential criterion
    ec = EssentialCriterion(
        id=str(uuid.uuid4()),
        form_template_id=form.id,
        code="ESS_ETHICS",
        label="Ethics",
        sort_order=1,
    )
    db.add(ec)

    db.commit()
    db.refresh(board)
    return board


def make_assessor(db, board_id: str, role_id="ROLE_LEAD") -> Assessor:
    """Create and persist a minimal assessor."""
    a = Assessor(
        id=str(uuid.uuid4()),
        board_id=board_id,
        employee_id=f"EMP-{uuid.uuid4().hex[:6]}",
        name="Test Assessor",
        email="test@qci.org",
        role_id=role_id,
        is_active=True,
        audit_count=0,
    )
    db.add(a)
    db.commit()
    db.refresh(a)
    return a


def make_assessment(db, board_id: str) -> Assessment:
    """Create and persist a minimal assessment."""
    from datetime import datetime
    a = Assessment(
        id=str(uuid.uuid4()),
        board_id=board_id,
        assessment_type="Initial",
        organization_name="Acme Labs",
        scheme="ISO/IEC 17025",
        status="IN_PROGRESS",
        assessment_date=datetime.utcnow(),
    )
    db.add(a)
    db.commit()
    db.refresh(a)
    return a


# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------
def make_user(db, role="super_admin", board_id=None) -> User:
    """Create and persist a test user."""
    u = User(
        id=str(uuid.uuid4()),
        email=f"test-{uuid.uuid4().hex[:6]}@qci.org",
        full_name="Test User",
        password_hash=hash_password("Test@1234"),
        role=role,
        board_id=board_id,
        is_active=True,
    )
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


def auth_headers(user: User) -> dict:
    """Return Bearer token headers for a given user."""
    token = create_access_token(user.id, user.email, user.role, user.board_id)
    return {"Authorization": f"Bearer {token}"}
