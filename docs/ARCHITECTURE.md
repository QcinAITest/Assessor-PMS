# QCI PMS — Architecture Document

**Version:** 1.1.0 | **Updated:** 2026-04-13

---

## 1. Overview

The **QCI Unified Performance Management System (PMS)** is a multi-board assessor performance evaluation platform for India's four accreditation boards: NABL, NABH, NABCB, and NABET. It manages the full lifecycle of assessor evaluation — form design, feedback collection via distributable links, weighted scoring, star ratings, and longitudinal cumulative ratings.

---

## 2. Tech Stack

| Layer | Technology |
|---|---|
| Backend framework | FastAPI (Python 3.10+, async-capable) |
| ORM | SQLAlchemy 2.x (declarative base) |
| Database | SQLite (dev) / PostgreSQL (prod) |
| Auth | JWT (HS256), bcrypt password hashing |
| Frontend | Jinja2 templates + Alpine.js + Tailwind CSS |
| Styling | Tailwind CSS (CDN), custom `qci-accent` color |
| Dark mode | Tailwind `dark:` classes, toggled via localStorage |

---

## 3. Repository Structure

```
qci-pms/
├── main.py                   App factory — router registration + page routes
├── migrate.py                Schema migration runner (ALTER TABLE scripts)
├── app/
│   ├── database.py           SQLAlchemy engine, SessionLocal, get_db()
│   ├── seed.py               First-run data seeder (boards, roles, forms, users)
│   ├── api/
│   │   ├── auth.py           Login, user CRUD, auth middleware
│   │   ├── boards.py         Board config, forms, parameters, essentials, frequency rules, webhooks
│   │   ├── assessments.py    Assessors, assessments, submissions, scoring
│   │   ├── programs.py       Service lines, programs, public form fill (no-auth token)
│   │   ├── integration.py    Portal event ingestion, status API, portal adapters, audit logs
│   │   └── sync.py           Bulk assessor/user sync from external portals
│   ├── models/
│   │   ├── auth.py           User model
│   │   ├── board.py          All board-related models + log_config_change() helper
│   │   └── program.py        ServiceLine, Program models
│   ├── schemas/
│   │   └── requests.py       All Pydantic request schemas
│   └── services/
│       ├── auth_service.py   JWT + bcrypt
│       ├── scoring_engine.py Form scoring, star rating, cumulative ratings
│       └── frequency_manager.py Frequency rule evaluation + form generation
├── templates/                Jinja2 HTML (Alpine.js SPA-style pages)
└── static/                   CSS, JS assets
```

---

## 4. Data Model

### Entity Relationship Summary

```
Board
 ├── BoardRole[]            (system_role_id ↔ display label per board)
 ├── FormTemplate[]
 │    ├── Parameter[]       (top-level + sub-parameters, self-referential)
 │    ├── EssentialCriterion[]  (mandatory YES/NO gates)
 │    └── FormSubmission[]
 ├── FrequencyRule[]        (when to auto-generate submissions)
 ├── Assessor[]             (employee_id as external sync anchor)
 │    ├── AuditScore[]
 │    └── CumulativeRating
 ├── Assessment[]
 │    └── FormSubmission[]
 ├── Webhook[]
 ├── PortalAdapter[]        (role/event translation maps)
 └── ServiceLine[]
      └── Program[]

User                        (portal login accounts — SYSTEM_ADMIN or BOARD_ADMIN)
AuditLog                    (all INBOUND / OUTBOUND / SYSTEM events)
```

### Key Design Decisions

| Decision | Rationale |
|---|---|
| Board config stored as JSON | Each board has unique star bands, rating engine, cumulative window — avoids sparse columns |
| `employee_id` as external sync anchor | Stable identifier from QCI HR systems; email can change |
| `User.external_id` for portal users | Maps login accounts to source system for sync idempotency |
| Self-referential `Parameter.parent_id` | Supports top-level evaluation areas + sub-questions in one table |
| `FormSubmission.submission_token` | Distributable public form link (like Google Forms) — no login needed |
| `stakeholder_weight` on FormTemplate | Multi-form weighted aggregation per assessor per audit |
| Soft-delete everywhere | `is_active=False` on Assessors and Users; never hard-delete |
| `AuditLog` for all integrations | Full traceability; INBOUND + OUTBOUND + SYSTEM directions |

---

## 5. Authentication & Authorization

### JWT Flow

```
POST /api/v1/auth/login
  → verify email + bcrypt password
  → create JWT (HS256, 8h expiry): { sub: user_id, email, role, board_id }
  → stored in localStorage as qci_token

All protected requests:
  Authorization: Bearer <token>
  → decoded by get_current_user()
```

### Role Model

| Role | Access |
|---|---|
| `SYSTEM_ADMIN` | All boards, all users, sync/users endpoint |
| `BOARD_ADMIN` | Own board only (enforced by `require_board_access`) |
| _(no auth)_ | Public form fill via `submission_token`, ingest endpoint, status check |

### `require_board_access` Logic

```python
if user.role == "SYSTEM_ADMIN":
    return user  # unrestricted

board = lookup(board_id or board_code)
if user.board_id != board.id:
    raise 403  # board admin cannot cross boards
```

---

## 6. Scoring Engine

### Per-Form Score (`scoring_engine.py`)

```
Form Score = Σ (parameter_weight_normalized × parameter_score)

parameter_score:
  RATING_1_5  → value as-is (1.0 – 5.0)
  YES_NO      → 5.0 if YES, 1.0 if NO
  PERCENTAGE  → value / 20 (maps 0-100 → 1-5)
  CALCULATED  → average of children (recursive)

essential_flag = True if ANY essential criterion answered "NO"
```

### Audit Score (cross-form aggregation)

```
Final Score = Σ (form.stakeholder_weight × form_score)
            ÷ Σ (stakeholder_weight of submitted forms)

base_100_score:
  numeric engine  → (score - 1) / 4 × 100
  percentage      → score directly

star_rating → mapped via Board.config.star_bands
```

### Cumulative Rating

```
Window = Board.config.cumulative_window (default: 10)
Cumulative Score = average of last N AuditScores for evaluee
has_essential_flags = any of those audits had essential_flag=True
```

---

## 7. Frequency Rules

Determines when feedback forms are auto-generated when an assessor completes an audit.

| Trigger Type | Logic |
|---|---|
| `EVERY_AUDIT` | Always generate |
| `POST_N_AUDITS` | Generate when `audit_count % trigger_value == 0` |
| `QUARTERLY` | Generate if no submission in last 90 days |
| `ANNUALLY` | Generate if no submission in last 365 days |
| `ON_EVENT` | Generate on specific assessment types (e.g., Re-assessment) |

**Flow:**
```
POST /api/v1/triggers/assessment-complete
  → evaluate_triggers() → which forms should be generated
  → create_pending_submissions() → FormSubmission(status=CREATED) rows
  → increment_audit_count() → assessor.audit_count++
```

---

## 8. Integration Architecture

### Push Model (QCI portal → PMS)

```
QCI Portal                        PMS
    │                              │
    │  POST /api/v1/ingest/{code}  │  ← generic event listener
    │  POST /api/v1/sync/boards/   │  ← bulk assessor sync
    │       {id}/assessors         │
    │  POST /api/v1/sync/users     │  ← bulk user sync
    │                              │
    │  GET  /api/v1/assessments/   │  ← portal polls before next stage
    │       {id}/status            │
```

### PortalAdapter (Role/Event Translation)

Each board can have multiple adapters — one per connected portal. The adapter holds:
- `role_map`: `{ "101": "ROLE_LEAD", "102": "ROLE_PEER" }` — external IDs → internal system_role_ids
- `event_map`: `{ "assessment_done": "ASSESSMENT_COMPLETE" }` — external event names → internal
- `vocabulary_map`: display-term translations for UI

Used by both `/api/v1/ingest/{board_code}` (event-based) and `/api/v1/sync/boards/{id}/assessors` (bulk sync).

### Public Form Distribution

```
Board Admin → POST .../forms/{id}/generate-link
           → creates FormSubmission(status=CREATED, token=uuid)
           → returns shareable URL: /forms/{token}

Assessor opens URL (no login required)
           → GET /api/v1/forms/{token} → form structure
           → POST /api/v1/forms/{token}/submit → scored, status=SUBMITTED
```

---

## 9. Submission State Machine

```
CREATED → SENT → PENDING → SUBMITTED
                         → FLAGGED  (if essential_flag=True)
```

Managed via `PATCH /api/v1/submissions/{id}/status`.

---

## 10. Frontend Architecture

All pages are Jinja2 templates with **Alpine.js** for reactive state. No build step required.

| Template | Role | Key Alpine component |
|---|---|---|
| `login.html` | All | `loginApp()` |
| `dashboard.html` | All | `dashboardApp()` — Board Comparison Matrix via `Promise.allSettled` |
| `sysadmin.html` | SYSTEM_ADMIN | `sysAdminApp()` — board/user management, sync modals |
| `board_admin.html` | BOARD_ADMIN | `boardAdminApp()` — assessors, forms, frequency, integrations |
| `form_builder.html` | BOARD_ADMIN | `formBuilder()` — parameter tree, essential criteria |
| `scoring.html` | BOARD_ADMIN | `scoringApp()` — score history, cumulative ratings |
| `public_form.html` | None (public) | `formFillApp()` — token-gated form fill + preview mode |

### API Client Pattern

All pages share a global `api` helper in `base.html`:
```javascript
api.get('/api/v1/...')      // → fetch with Bearer token
api.post('/api/v1/...', {}) // → fetch with JSON body + Bearer
api.put('/api/v1/...', {})
api.del('/api/v1/...')
```
JWT is read from `localStorage.getItem('qci_token')` on every call.

---

## 11. Database Migrations

`migrate.py` runs idempotent schema migrations and **auto-detects the database backend** from `DATABASE_URL`:

| Backend | Driver | Behaviour |
|---|---|---|
| SQLite (default) | `sqlite3` | `ADD COLUMN` only; constraint changes via table recreation with zero-row safety check |
| PostgreSQL | `psycopg2` | `ADD COLUMN` + `ALTER COLUMN … DROP NOT NULL` — no table recreation needed |

**Run:** `python3 migrate.py`

Set `DATABASE_URL` before running if using PostgreSQL:
```bash
# Windows PowerShell
$env:DATABASE_URL="postgresql://user:pass@localhost:5432/dbname"
python migrate.py

# macOS / Linux
export DATABASE_URL="postgresql://user:pass@localhost:5432/dbname"
python3 migrate.py
```

Current migrations:
- `board_roles`, `essential_criteria`, `frequency_rules`: add `created_at`, `updated_at`
- `webhooks`, `assessors`: add `updated_at`
- `form_submissions`: make `assessment_id`, `evaluator_id`, `evaluee_id` nullable
- `users`: add `external_id VARCHAR(100)`

---

## 12. Deployment

| Env Var | Default | Purpose |
|---|---|---|
| `DATABASE_URL` | `sqlite:///./qci_pms.db` | Database connection string |
| `QCI_SECRET_KEY` | `qci-pms-dev-secret-...` | JWT signing key (change in prod) |

**Local dev (SQLite):**
```bash
pip install -r requirements.txt
python3 migrate.py
python3 -c "from app.seed import seed; seed()"   # first run only
uvicorn main:app --reload
```

**Production (PostgreSQL):**
```bash
pip install psycopg2-binary
export DATABASE_URL="postgresql://user:pass@host:5432/dbname"
python3 migrate.py
python3 -c "from app.seed import seed; seed()"   # first run only
uvicorn main:app --host 0.0.0.0 --port 8000
```

Serve behind nginx/gunicorn in production; set a strong `QCI_SECRET_KEY`.
