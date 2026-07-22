# QCI PMS — API Reference

**Base URL:** `http://localhost:8000`  
**Version:** 1.1.0 | **Updated:** 2026-04-13  
**Interactive docs:** `/docs` (Swagger UI)

---

## Authentication

All protected endpoints require a Bearer token in the `Authorization` header:

```
Authorization: Bearer <jwt_token>
```

Obtain a token via `POST /api/v1/auth/login`.

**Auth levels used in this document:**

| Level | Description |
|---|---|
| None | No authentication required |
| Token | Any valid JWT |
| Board Access | SYSTEM_ADMIN (any board) or BOARD_ADMIN (own board only) |
| System Admin | SYSTEM_ADMIN role only |

---

## Auth Endpoints

### POST /api/v1/auth/login
Login and receive a JWT token.

**Auth:** None

**Request:**
```json
{ "email": "admin@qci.org.in", "password": "Admin@123" }
```

**Response:**
```json
{
  "access_token": "eyJ...",
  "token_type": "bearer",
  "user": {
    "id": "uuid", "email": "admin@qci.org.in",
    "full_name": "QCI Admin", "role": "SYSTEM_ADMIN",
    "board_id": null, "is_active": true
  }
}
```

---

### GET /api/v1/auth/me
Returns the currently authenticated user.

**Auth:** Token

---

### GET /api/v1/auth/users
List all system users (board admins + system admins).

**Auth:** System Admin

**Response:** `[{ id, email, full_name, role, board_id, is_active, created_at, last_login }]`

---

### POST /api/v1/auth/users
Create a new portal login account.

**Auth:** System Admin

**Request:**
```json
{
  "email": "nabl.admin@qci.org.in",
  "full_name": "NABL Admin",
  "password": "Temp@pass123",
  "role": "BOARD_ADMIN",
  "board_id": "uuid-of-nabl-board"
}
```

---

### PUT /api/v1/auth/users/{user_id}
Update a user's details.

**Auth:** System Admin

**Request** (all fields optional):
```json
{
  "full_name": "Updated Name",
  "password": "NewPass@456",
  "is_active": true,
  "board_id": "uuid"
}
```

---

### DELETE /api/v1/auth/users/{user_id}
Deactivate a user account (sets `is_active=false`).

**Auth:** System Admin

---

## Board Endpoints

### GET /api/v1/boards
List boards. BOARD_ADMIN sees only their own board; SYSTEM_ADMIN sees all.

**Auth:** Token

**Response:**
```json
[{
  "id": "uuid", "code": "NABL", "name": "National Accreditation Board...",
  "is_active": true, "forms_count": 5, "roles_count": 6,
  "config": { "rating_engine": "numeric", "star_bands": [...], "cumulative_window": 10 }
}]
```

---

### POST /api/v1/boards
Create a new board.

**Auth:** System Admin

**Request:**
```json
{
  "code": "NABL",
  "name": "National Accreditation Board for Testing and Calibration Laboratories",
  "description": "...",
  "logo_url": "https://...",
  "config": {
    "rating_engine": "numeric",
    "star_bands": [
      { "min": 4.5, "star": 5 }, { "min": 3.5, "star": 4 },
      { "min": 2.5, "star": 3 }, { "min": 1.5, "star": 2 }, { "min": 0, "star": 1 }
    ],
    "cumulative_window": 10
  }
}
```

---

### GET /api/v1/boards/{board_id}
Get full board detail including forms, roles, frequency rules.

**Auth:** Board Access

---

### PUT /api/v1/boards/{board_id}
Update board metadata or config (partial update).

**Auth:** Board Access

---

## Role Mapping Endpoints

### GET /api/v1/boards/{board_id}/roles
List all role mappings for the board.

**Auth:** Board Access

**Response:**
```json
[{
  "id": 1, "board_id": "uuid", "system_role_id": "ROLE_LEAD",
  "display_label": "Lead Assessor", "description": "...",
  "can_be_evaluator": true, "can_be_evaluee": true
}]
```

---

### POST /api/v1/boards/{board_id}/roles
Add a role mapping.

**Auth:** Board Access

**Request:**
```json
{
  "system_role_id": "ROLE_LEAD",
  "display_label": "Lead Assessor",
  "description": "Leads the assessment team",
  "can_be_evaluator": true,
  "can_be_evaluee": true
}
```

---

### PUT /api/v1/boards/{board_id}/roles/{role_id}
Update a role mapping (partial).

**Auth:** Board Access

---

### DELETE /api/v1/boards/{board_id}/roles/{role_id}
Delete a role mapping.

**Auth:** Board Access

---

## Form Template Endpoints

### GET /api/v1/boards/{board_id}/forms
List all form templates for the board.

**Auth:** Board Access

---

### POST /api/v1/boards/{board_id}/forms
Create a form template.

**Auth:** Board Access

**Request:**
```json
{
  "code": "F1_CAB",
  "name": "CAB Assessment Form",
  "description": "Evaluation of Lead Assessor by CAB",
  "stakeholder_weight": 0.4,
  "target_evaluator_role": "ROLE_LEAD",
  "target_evaluee_roles": ["ROLE_LEAD", "ROLE_PEER"],
  "is_mandatory": true
}
```

> Note: Sum of `stakeholder_weight` across all forms must not exceed 1.0.

---

### GET /api/v1/boards/{board_id}/forms/{form_id}
Get full form structure including parameters and essential criteria.

**Auth:** Board Access

**Response:**
```json
{
  "id": "uuid", "code": "F1_CAB", "name": "...", "version": 1,
  "stakeholder_weight": 0.4, "is_mandatory": true,
  "parameters": [
    {
      "id": "uuid", "code": "C1", "label": "Technical Competence",
      "weight": 30, "data_type": "CALCULATED", "sort_order": 0,
      "children": [
        { "id": "uuid", "code": "C1_Sub1", "label": "Knowledge of standards",
          "data_type": "RATING_1_5", "is_mandatory": true }
      ]
    }
  ],
  "essential_criteria": [
    { "id": "uuid", "code": "ESS_ETHICS", "label": "Maintains professional ethics", "sort_order": 0 }
  ]
}
```

---

### PUT /api/v1/boards/{board_id}/forms/{form_id}
Update a form template. Increments `version`.

**Auth:** Board Access

---

### DELETE /api/v1/boards/{board_id}/forms/{form_id}
Delete a form template. Blocked if real (non-preview) submissions exist.

**Auth:** Board Access

---

### POST /api/v1/boards/{board_id}/forms/{form_id}/generate-link
Generate a public distribution link for the form.

**Auth:** Board Access

**Response:**
```json
{ "token": "uuid", "url": "/forms/uuid" }
```

---

## Parameter Endpoints

### POST /api/v1/boards/{board_id}/forms/{form_id}/parameters
Add a parameter (evaluation area or sub-question).

**Auth:** Board Access

**Request:**
```json
{
  "code": "C1",
  "label": "Technical Competence",
  "weight": 30,
  "data_type": "CALCULATED",
  "parent_id": null,
  "is_mandatory": true,
  "sort_order": 0
}
```

**data_type values:** `RATING_1_5` · `YES_NO` · `PERCENTAGE` · `TEXT` · `DROPDOWN` · `CALCULATED`

> Top-level parameters (`parent_id=null`) should use `CALCULATED` (auto-averaged from children).  
> Sum of `weight` across top-level parameters should be 100.

---

### PUT /api/v1/boards/{board_id}/forms/{form_id}/parameters/{param_id}
Update a parameter.

**Auth:** Board Access

---

### DELETE /api/v1/boards/{board_id}/forms/{form_id}/parameters/{param_id}
Delete a parameter and all its children.

**Auth:** Board Access

---

### GET /api/v1/boards/{board_id}/forms/{form_id}/normalized-weights
Get auto-normalized top-level parameter weights (sum = 1.0).

**Auth:** Board Access

**Response:**
```json
{
  "form_id": "uuid",
  "normalized_weights": { "C1": 0.30, "C2": 0.25, "C3": 0.20, "C4": 0.25 },
  "sum_check": 1.0
}
```

---

## Essential Criteria Endpoints

### POST /api/v1/boards/{board_id}/forms/{form_id}/essentials
Add a mandatory YES/NO gate criterion.

**Auth:** Board Access

**Request:**
```json
{ "code": "ESS_ETHICS", "label": "Maintains professional ethics and conduct", "sort_order": 0 }
```

> If any essential criterion is answered "No" on a submission, `essential_flag=True` is set, triggering a review flag regardless of numeric score.

---

### PUT /api/v1/boards/{board_id}/forms/{form_id}/essentials/{ec_id}
Update an essential criterion.

**Auth:** Board Access

**Request** (all fields optional):
```json
{ "code": "ESS_ETHICS", "label": "Updated criterion text", "sort_order": 1 }
```

---

### DELETE /api/v1/boards/{board_id}/forms/{form_id}/essentials/{ec_id}
Delete an essential criterion.

**Auth:** Board Access

---

## Frequency Rule Endpoints

### GET /api/v1/boards/{board_id}/frequency-rules
List all frequency rules for the board.

**Auth:** Board Access

**Response:**
```json
[{
  "id": 1, "board_id": "uuid", "role_id": "ROLE_LEAD",
  "form_template_id": "uuid", "trigger_type": "EVERY_AUDIT",
  "trigger_value": null, "is_active": true
}]
```

---

### POST /api/v1/boards/{board_id}/frequency-rules
Add a frequency rule.

**Auth:** Board Access

**Request:**
```json
{
  "role_id": "ROLE_LEAD",
  "form_template_id": "uuid",
  "trigger_type": "POST_N_AUDITS",
  "trigger_value": 3,
  "is_active": true
}
```

**trigger_type values:**

| Value | Meaning |
|---|---|
| `EVERY_AUDIT` | Generate form after every audit |
| `POST_N_AUDITS` | Generate every Nth audit (`trigger_value` = N) |
| `QUARTERLY` | Generate if no submission in last 90 days |
| `ANNUALLY` | Generate if no submission in last 365 days |
| `ON_EVENT` | Generate on specific assessment types |

---

### PUT /api/v1/boards/{board_id}/frequency-rules/{rule_id}
Update a frequency rule.

**Auth:** Board Access

---

### DELETE /api/v1/boards/{board_id}/frequency-rules/{rule_id}
Delete a frequency rule.

**Auth:** Board Access

---

## Webhook Endpoints

### GET /api/v1/boards/{board_id}/webhooks
List all webhooks.

**Auth:** Board Access

---

### POST /api/v1/boards/{board_id}/webhooks
Register a webhook.

**Auth:** Board Access

**Request:**
```json
{
  "event_type": "SCORE_CALCULATED",
  "target_url": "https://portal.qci.org/webhooks/pms",
  "secret": "hmac-secret-string",
  "is_active": true
}
```

**event_type values:** `ASSESSMENT_CREATED` · `FEEDBACK_DUE` · `SCORE_CALCULATED` · `ESSENTIAL_FLAGGED`

---

### PUT /api/v1/boards/{board_id}/webhooks/{hook_id}
Update a webhook.

**Auth:** Board Access

---

### DELETE /api/v1/boards/{board_id}/webhooks/{hook_id}
Delete a webhook.

**Auth:** Board Access

---

## Assessor Endpoints

### GET /api/v1/boards/{board_id}/assessors
List all assessors for the board.

**Auth:** Board Access

**Response:**
```json
[{
  "id": "uuid", "employee_id": "EMP001", "name": "Ravi Kumar",
  "email": "ravi@qci.org", "role_id": "ROLE_LEAD",
  "is_active": true, "audit_count": 12
}]
```

---

### POST /api/v1/boards/{board_id}/assessors
Create an assessor.

**Auth:** Board Access

**Request:**
```json
{
  "employee_id": "EMP001",
  "name": "Ravi Kumar",
  "email": "ravi@qci.org",
  "phone": "+91 98765 43210",
  "role_id": "ROLE_LEAD"
}
```

---

### PUT /api/v1/boards/{board_id}/assessors/{assessor_id}
Update an assessor (partial).

**Auth:** Board Access

---

### DELETE /api/v1/boards/{board_id}/assessors/{assessor_id}
Deactivate an assessor (soft-delete; sets `is_active=False`).

**Auth:** Board Access

**Response:** `{ "deactivated": true, "id": "uuid" }`

---

## Assessment Endpoints

### GET /api/v1/boards/{board_id}/assessments
List all assessments for the board (newest first).

**Auth:** Board Access

---

### POST /api/v1/boards/{board_id}/assessments
Create a new assessment event.

**Auth:** Board Access

**Request:**
```json
{
  "assessment_type": "Initial",
  "organization_name": "XYZ Testing Lab Pvt Ltd",
  "scheme": "Testing Laboratories",
  "standard_version": "ISO/IEC 17025:2017",
  "assessment_date": "2026-04-15T09:00:00"
}
```

**assessment_type values:** `Initial` · `Surveillance` · `Re-assessment` · `Extension` · `Onsite`

---

## Submission Endpoints

### POST /api/v1/assessments/{assessment_id}/submissions
Submit a filled evaluation form. Auto-calculates score.

**Auth:** None

**Request:**
```json
{
  "form_template_id": "uuid",
  "evaluator_id": "uuid",
  "evaluee_id": "uuid",
  "responses": {
    "C1_Sub1": 4,
    "C1_Sub2": 5,
    "C2": 3,
    "ESS_ETHICS": "YES",
    "ESS_CONFIDENTIALITY": "NO"
  },
  "comments": "Demonstrated strong technical knowledge."
}
```

**Response:**
```json
{
  "id": "uuid",
  "form_score": 3.85,
  "essential_flag": true,
  "status": "FLAGGED"
}
```

> `essential_flag=true` when any essential criterion response is `"NO"`.

---

### GET /api/v1/assessments/{assessment_id}/submissions
List all submissions for an assessment.

**Auth:** None

---

### PATCH /api/v1/submissions/{submission_id}/status
Advance a submission through its state machine.

**Auth:** None

**Query param:** `?new_status=SENT`

**State transitions:** `CREATED → SENT → PENDING → SUBMITTED / FLAGGED`

---

## Scoring Endpoints

### POST /api/v1/assessments/{assessment_id}/calculate-score
Aggregate all form submissions into a final audit score for one evaluee.

**Auth:** None

**Query param:** `?evaluee_id=uuid`

**Response:**
```json
{
  "audit_score_id": "uuid",
  "final_score": 3.92,
  "base_100_score": 73.0,
  "star_rating": 4,
  "essential_flag": false,
  "form_scores": {
    "F1_CAB":  { "score": 4.1, "weight": 0.4 },
    "F2_LEAD": { "score": 3.7, "weight": 0.3 }
  }
}
```

---

### GET /api/v1/assessors/{assessor_id}/cumulative-rating
Get the rolling average of the assessor's last N audits.

**Auth:** None

**Response:**
```json
{
  "cumulative_score": 3.88,
  "star_rating": 4,
  "window_size": 10,
  "has_essential_flags": false
}
```

---

### GET /api/v1/assessors/{assessor_id}/score-history
Get historical audit scores (last 50).

**Auth:** None

---

## Integration Trigger Endpoints

### POST /api/v1/triggers/assessment-complete
Called by an external portal when an assessment is done. Generates pending feedback forms per frequency rules.

**Auth:** None

**Request:**
```json
{
  "assessment_id": "uuid",
  "evaluee_ids": ["uuid1", "uuid2"],
  "source_portal": "nabl-portal-v2"
}
```

**Response:**
```json
{
  "assessment_id": "uuid",
  "evaluees": [
    {
      "evaluee_id": "uuid1",
      "audit_count": 13,
      "forms_generated": 2,
      "details": [{ "form_code": "F1_CAB", "submission_id": "uuid", "token": "uuid" }]
    }
  ]
}
```

---

### POST /api/v1/ingest/{board_code}
Generic event listener for external portal integrations. Routes events based on `event_type`.

**Auth:** None

**Request:**
```json
{
  "event_type": "ASSESSMENT_COMPLETE",
  "portal_id": "nabl-portal-v2",
  "data": {
    "assessment_id": "uuid",
    "evaluee_ids": ["uuid1"]
  }
}
```

**Response:** `{ "log_id": 42, "event_type": "ASSESSMENT_COMPLETE", "result": { ... } }`

---

### GET /api/v1/assessments/{assessment_id}/status
Blocking status check — portals poll this before advancing to the next workflow stage.

**Auth:** None

**Response:**
```json
{
  "assessment_id": "uuid",
  "workflow_state": "PENDING_FEEDBACK",
  "pending_feedback": true,
  "pending_count": 3,
  "blocked_forms": ["F1_CAB", "F2_LEAD"],
  "completed_count": 1
}
```

---

## Programs & Service Lines

### GET /api/v1/boards/{board_id}/service-lines
List all service lines.

**Auth:** Token

---

### POST /api/v1/boards/{board_id}/service-lines
Create a service line.

**Auth:** Token

**Request:**
```json
{ "code": "SL_TEST", "name": "Testing Laboratories", "description": "...", "sort_order": 0 }
```

---

### DELETE /api/v1/boards/{board_id}/service-lines/{sl_id}
Delete a service line (and all its programs).

**Auth:** Token

---

### GET /api/v1/boards/{board_id}/service-lines/{sl_id}/programs
List programs under a service line.

**Auth:** Token

---

### POST /api/v1/boards/{board_id}/service-lines/{sl_id}/programs
Create a program.

**Auth:** Token

**Request:**
```json
{
  "code": "P_ISO17025T",
  "name": "ISO/IEC 17025 – Testing",
  "description": "Testing laboratory accreditation",
  "standard_version": "ISO/IEC 17025:2017",
  "sort_order": 0
}
```

---

### DELETE /api/v1/boards/{board_id}/service-lines/{sl_id}/programs/{program_id}
Delete a program.

**Auth:** Token

---

## Public Form Fill (No Auth)

### GET /api/v1/forms/{token}
Fetch form structure for a distributed form link.

**Auth:** None (token is the credential)

**Response:**
```json
{
  "already_submitted": false,
  "submission_id": "uuid",
  "form_name": "CAB Assessment Form",
  "form_code": "F1_CAB",
  "evaluee_name": "Ravi Kumar",
  "parameters": [...],
  "essential_criteria": [...]
}
```

---

### POST /api/v1/forms/{token}/submit
Submit a public form.

**Auth:** None

**Request:**
```json
{
  "responses": { "C1_Sub1": 4, "ESS_ETHICS": "YES" },
  "comments": "Good performance overall."
}
```

**Response:**
```json
{ "message": "Form submitted successfully", "form_score": 3.85, "essential_flag": false }
```

---

## Portal Adapter Endpoints

### GET /api/v1/boards/{board_id}/portal-adapters
List portal adapters for the board.

**Auth:** None

---

### POST /api/v1/boards/{board_id}/portal-adapters
Create a portal adapter (role/event translation layer).

**Auth:** None

**Request:**
```json
{
  "portal_id": "nabl-portal-v2",
  "role_map": { "101": "ROLE_LEAD", "102": "ROLE_PEER", "103": "ROLE_TE" },
  "event_map": { "assessment_done": "ASSESSMENT_COMPLETE" },
  "vocabulary_map": { "Technical Expert": "Technical Expert (TE)" },
  "is_active": true
}
```

---

### PUT /api/v1/boards/{board_id}/portal-adapters/{adapter_id}
Update an adapter (partial).

**Auth:** None

---

### DELETE /api/v1/boards/{board_id}/portal-adapters/{adapter_id}
Delete a portal adapter.

**Auth:** None

---

### GET /api/v1/boards/{board_id}/audit-logs
View integration audit logs for the board.

**Auth:** None

**Query params:**
- `direction` — `INBOUND` · `OUTBOUND` · `SYSTEM` (optional filter)
- `status` — `received` · `processed` · `failed` · `dispatched` (optional filter)
- `limit` — max records to return (default 50)

---

## Sync Endpoints

### POST /api/v1/sync/boards/{board_id}/assessors
Bulk upsert assessors from an external portal. Idempotent — safe to call repeatedly.

**Auth:** Board Access

**Request:**
```json
{
  "portal_id": "nabl-portal-v2",
  "deactivate_missing": false,
  "assessors": [
    {
      "employee_id": "EMP001",
      "name": "Ravi Kumar",
      "email": "ravi@qci.org",
      "role_id": "ROLE_LEAD",
      "is_active": true
    },
    {
      "employee_id": "EMP002",
      "name": "Priya Singh",
      "role_id": "101"
    }
  ]
}
```

> If `portal_id` is provided, `role_id` values are translated via the board's `PortalAdapter.role_map` (e.g., `"101"` → `"ROLE_LEAD"`).  
> If `deactivate_missing=true`, all assessors for this board whose `employee_id` is **not** in the payload are soft-deactivated.

**Response:**
```json
{ "created": 12, "updated": 5, "deactivated": 0, "errors": [] }
```

---

### POST /api/v1/sync/users
Bulk upsert portal login accounts. Idempotent by email. New users receive a one-time temporary password.

**Auth:** System Admin

**Request:**
```json
{
  "users": [
    {
      "email": "nabl.admin@qci.org.in",
      "full_name": "Priya Sharma",
      "role": "BOARD_ADMIN",
      "board_code": "NABL",
      "external_id": "QCI-STAFF-099"
    },
    {
      "email": "sysadmin@qci.org.in",
      "full_name": "Amit Joshi",
      "role": "SYSTEM_ADMIN"
    }
  ]
}
```

> `role` values: `BOARD_ADMIN` · `SYSTEM_ADMIN`  
> `board_code` is required when `role=BOARD_ADMIN`. Must match an existing board code (e.g., `NABL`, `NABH`).  
> Existing users: updates `full_name`, `board_id`, `external_id`. **Never overwrites password.**  
> New users: auto-generates `Temp@{random8}` password, returned in `new_credentials` (shown once — not stored in plaintext).

**Response:**
```json
{
  "created": 2,
  "updated": 1,
  "errors": [],
  "new_credentials": [
    { "email": "nabl.admin@qci.org.in", "temp_password": "Temp@xK9mP2qL" }
  ]
}
```

---

## Error Responses

All endpoints return standard HTTP error responses:

| Status | Meaning |
|---|---|
| 400 | Bad request / validation error |
| 401 | Missing or invalid token |
| 403 | Insufficient permissions (wrong role or wrong board) |
| 404 | Resource not found |
| 422 | Pydantic validation failure (see `detail` field) |
| 500 | Internal server error |

**Error body:**
```json
{ "detail": "Human-readable error message" }
```
