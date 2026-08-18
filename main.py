import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse

from app.database import engine, Base
from app.models.board import *      # noqa: ensure all board models registered
from app.models.auth import User    # noqa
from app.models.program import ServiceLine, Program  # noqa
from app.api.boards import router as boards_router
from app.api.assessments import router as assessments_router
from app.api.integration import router as integration_router
from app.api.auth import router as auth_router
from app.api.programs import router as programs_router, public_router
from app.api.sync import router as sync_router

Base.metadata.create_all(bind=engine)


def _ensure_schema():
    """Idempotent ADD COLUMN for tables that predate newer model fields.

    create_all() creates missing tables but never ALTERs existing ones, so a
    column added to an already-deployed table must be backfilled explicitly —
    otherwise every query that selects that model 500s (e.g. deleting a form
    runs a count over form_submissions). Type names below are accepted by both
    PostgreSQL and SQLite.
    """
    from sqlalchemy import inspect as sa_inspect, text
    required = {
        "assessments": {
            "application_id": "VARCHAR(100)",
        },
        "form_submissions": {
            "submission_token": "VARCHAR(36)",
            "token_expires_at": "TIMESTAMP",
            "evaluator_email": "VARCHAR(300)",
            "form_snapshot": "JSON",
            "evaluee_role": "VARCHAR(50)",
        },
        "parameters": {
            "applies_to_roles": "JSON",
        },
    }
    # Free-text columns that were originally VARCHAR(n) and overflowed on Postgres
    # (e.g. an auto-generated parameter code longer than 50 chars). Converting to
    # TEXT removes the length limit for good. Postgres-only: VARCHAR->TEXT is a
    # cheap metadata change; SQLite ignores length so it needs nothing.
    to_text = {
        "parameters": ["code", "label"],
        "essential_criteria": ["code", "label"],
        "form_templates": ["name"],
        "assessments": ["organization_name", "scheme", "standard_version"],
    }
    insp = sa_inspect(engine)
    for table, cols in required.items():
        try:
            existing = {c["name"] for c in insp.get_columns(table)}
        except Exception:
            continue  # table doesn't exist yet — create_all handles it
        for name, ddl in cols.items():
            if name not in existing:
                try:
                    with engine.begin() as conn:
                        conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}"))
                except Exception:
                    pass  # never block startup on a best-effort backfill
    if engine.dialect.name == "postgresql":
        for table, cols in to_text.items():
            try:
                colinfo = {c["name"]: c["type"] for c in insp.get_columns(table)}
            except Exception:
                continue
            for name in cols:
                t = colinfo.get(name)
                # length is not None => still a VARCHAR(n); TEXT reports length None.
                if t is not None and getattr(t, "length", None) is not None:
                    try:
                        with engine.begin() as conn:
                            conn.execute(text(f"ALTER TABLE {table} ALTER COLUMN {name} TYPE TEXT"))
                    except Exception:
                        pass


_ensure_schema()

app = FastAPI(
    title="QCI Unified Performance Management System",
    description="Multi-board assessor performance evaluation platform for NABL, NABH, NABCB, NABET",
    version="1.1.0",
)

app.include_router(auth_router)
app.include_router(boards_router)
app.include_router(assessments_router)
app.include_router(integration_router)
app.include_router(programs_router)
app.include_router(public_router)
app.include_router(sync_router)

app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")


# --------------------------------------------------------------------------- #
# Auth-protected page routes (client-side auth check via localStorage JWT)    #
# --------------------------------------------------------------------------- #

@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return templates.TemplateResponse("login.html", {"request": request})


@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    return templates.TemplateResponse("dashboard.html", {"request": request})


@app.get("/sysadmin", response_class=HTMLResponse)
async def sysadmin(request: Request):
    return templates.TemplateResponse("sysadmin.html", {"request": request})


@app.get("/board/{board_code}", response_class=HTMLResponse)
async def board_admin(request: Request, board_code: str):
    return templates.TemplateResponse("board_admin.html", {
        "request": request, "board_code": board_code
    })


@app.get("/board/{board_code}/form/{form_id}", response_class=HTMLResponse)
async def form_builder(request: Request, board_code: str, form_id: str):
    return templates.TemplateResponse("form_builder.html", {
        "request": request, "board_code": board_code, "form_id": form_id
    })


@app.get("/board/{board_code}/scoring", response_class=HTMLResponse)
async def scoring_dashboard(request: Request, board_code: str):
    return templates.TemplateResponse("scoring.html", {
        "request": request, "board_code": board_code
    })


@app.get("/board/{board_code}/assessors/{assessor_id}", response_class=HTMLResponse)
async def assessor_profile(request: Request, board_code: str, assessor_id: str):
    """Performance card — longitudinal score history for a single assessor."""
    return templates.TemplateResponse("assessor_profile.html", {
        "request": request, "board_code": board_code, "assessor_id": assessor_id
    })


@app.get("/forms/{token}", response_class=HTMLResponse)
async def public_form_page(request: Request, token: str):
    """Public, no-auth form fill page — sent as a link to assessors."""
    return templates.TemplateResponse("public_form.html", {
        "request": request, "token": token, "preview_mode": False
    })


@app.get("/board/{board_code}/form/{form_id}/preview", response_class=HTMLResponse)
async def form_preview(request: Request, board_code: str, form_id: str):
    """Read-only preview of a form template — used by board admins before distribution."""
    return templates.TemplateResponse("public_form.html", {
        "request": request, "token": "", "preview_mode": True,
        "preview_board_code": board_code, "preview_form_id": form_id
    })


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
