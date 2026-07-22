# QCI Unified Performance Management System
## High-Level Requirements Document

**Organisation:** Quality Council of India (QCI)
**Product:** Assessor Performance Management System (PMS)
**Version:** 1.1
**Date:** April 2026
**Status:** In Development

---

## 1. Background & Problem Statement

QCI oversees four national accreditation boards — NABL, NABH, NABCB, and NABET — each of which deploys teams of external assessors to evaluate accreditation-seeking organisations. Currently, assessor performance feedback is collected manually (paper or email), scored inconsistently across boards, and provides no longitudinal view of an assessor's improvement over time.

Key pain points:
- No standardised digital form for assessor evaluation
- No single dashboard to compare performance across boards
- Assessors and data exist in QCI's HR/portal systems but must be manually re-entered into any new tool
- Board AMC members (admins) have no self-service tool to configure their own evaluation criteria

---

## 2. Objectives

1. Provide a **single configurable platform** that serves all four boards with board-specific forms, roles, scoring models, and terminology
2. Enable **data-driven performance tracking** — per-audit scores, star ratings, and rolling cumulative ratings
3. Allow **frictionless feedback collection** via public distributable form links (no assessor login required)
4. Automate **form generation triggers** based on configurable frequency rules
5. Integrate with **QCI's existing portal systems** via push-based sync rather than requiring manual data entry
6. Enforce **strict data isolation** — a board admin can only see and manage their own board's data

---

## 3. Stakeholders & User Roles

| Role | Who | What they do in PMS |
|---|---|---|
| System Admin | QCI HQ IT team | Creates boards, manages all users, runs bulk sync, views global audit log |
| Board Admin (AMC Member) | Per-board Programme Management staff | Configures forms, roles, assessors, frequency rules for their board |
| Assessor | External domain expert deployed on audits | Receives public form link; fills and submits evaluation form (no account needed) |
| QCI Portal | External HR/assessment management system | Pushes assessor and user data into PMS via sync API |

---

## 4. Scope

### 4.1 In Scope

**Board Configuration**
- CRUD for boards (NABL, NABH, NABCB, NABET) with per-board config (rating engine, star bands, cumulative window, terminology)
- Role mapping — translate generic internal role IDs (ROLE_LEAD, ROLE_PEER) to board-specific display labels
- Service lines and accreditation programs per board

**Form Builder**
- Visual form builder: add/edit/delete evaluation areas (top-level parameters) with percentage weights
- Sub-questions under each area with response type selection (Rating 1–5, Yes/No, Percentage, Free Text)
- Essential Criteria: mandatory YES/NO gates — any "No" flags the form regardless of numeric score
- Form versioning on every update
- Preview form before distribution

**Form Distribution & Collection**
- Generate shareable public links (token-based, no login required) per form per assessment
- Assessors fill forms via browser; auto-scored on submit
- Submission status state machine: CREATED → SENT → PENDING → SUBMITTED / FLAGGED

**Scoring Engine**
- Per-form weighted score (parameters × weights, auto-normalised if weights don't sum to 100%)
- Per-audit final score: weighted aggregation across all submitted forms for one assessor
- Cross-board normalised base-100 score for comparison
- Star rating mapped from score via board-specific bands (1–5 stars)
- Cumulative rating: rolling average of last N audits (configurable per board)
- Essential flag propagation: any flagged submission flags the audit score

**Frequency Rules**
- Configurable rules per (board, role, form): EVERY_AUDIT, POST_N_AUDITS, QUARTERLY, ANNUALLY, ON_EVENT
- Auto-generate pending form submissions when an assessment is marked complete

**Integration**
- Generic event ingestion endpoint (`POST /api/v1/ingest/{board_code}`) for external portals
- Assessment status check endpoint for portal polling
- Portal adapter layer: configurable role/event/vocabulary translation maps per portal per board
- Webhook registration for outbound events (SCORE_CALCULATED, ESSENTIAL_FLAGGED, etc.)
- Audit log for all INBOUND, OUTBOUND, and SYSTEM events

**Sync**
- Bulk assessor sync (`POST /api/v1/sync/boards/{id}/assessors`): upsert by employee_id, optional deactivate-missing, role translation via portal adapter
- Bulk admin user sync (`POST /api/v1/sync/users`): upsert by email, auto-generated temporary passwords for new accounts

**Dashboard**
- Board Comparison Matrix: all boards side-by-side showing forms, roles, assessors, programs
- Per-board drill-down: assessor list, score history, cumulative ratings, form distribution

**Access Control**
- JWT-based auth (8-hour tokens)
- SYSTEM_ADMIN: unrestricted access across all boards
- BOARD_ADMIN: scoped strictly to own board — cannot view or modify other boards' data
- Public form fill: token-based, no authentication required

### 4.2 Out of Scope (v1)

- Mobile native application
- Outbound webhook delivery (endpoint registered but dispatch not yet implemented)
- Assessor self-service portal (login, view own scores)
- Multi-language / regional language support
- Direct database integration with QCI portal (push model only — portal calls PMS)

---

## 5. Functional Requirements

### FR-1: Multi-Board Configuration
- FR-1.1: Each board shall have an independent configuration profile stored as structured JSON (rating engine, star bands, cumulative window, stakeholder weights)
- FR-1.2: Each board shall define its own role taxonomy mapped to internal system role IDs
- FR-1.3: Board admins shall only access their assigned board; system admins access all boards

### FR-2: Form Builder
- FR-2.1: Board admins shall be able to create evaluation forms with a name, code, and stakeholder weight
- FR-2.2: Forms shall support a two-level parameter hierarchy (evaluation areas → sub-questions)
- FR-2.3: Sub-questions shall support five response types: Rating 1–5, Yes/No, Percentage, Free Text, Dropdown
- FR-2.4: Top-level parameters shall carry a percentage weight; the system shall auto-normalise weights if they do not sum to 100%
- FR-2.5: Each form shall support zero or more Essential Criteria (mandatory YES/NO statements); a single "No" answer flags the entire form
- FR-2.6: Forms shall be versioned; any edit increments the version number
- FR-2.7: Board admins shall be able to preview a form before distribution

### FR-3: Form Distribution
- FR-3.1: Board admins shall generate a unique shareable URL per form per assessment
- FR-3.2: Form fill shall require no assessor login — the token in the URL is the credential
- FR-3.3: A submitted token shall be non-reusable (re-opening shows "already submitted")

### FR-4: Scoring
- FR-4.1: Form score shall be calculated automatically on submission
- FR-4.2: Final audit score shall aggregate multiple forms using their stakeholder weights
- FR-4.3: All scores shall be normalised to a 0–100 base for cross-board comparison
- FR-4.4: Star ratings (1–5) shall be derived from the board's configured score bands
- FR-4.5: Cumulative rating shall be a rolling average of the last N audits (N configurable per board; default 10)
- FR-4.6: Essential flags shall propagate from submission → audit score → cumulative rating

### FR-5: Frequency Rules
- FR-5.1: The system shall support five trigger types: EVERY_AUDIT, POST_N_AUDITS, QUARTERLY, ANNUALLY, ON_EVENT
- FR-5.2: When an assessment-complete event is received, the system shall evaluate all active rules for each evaluee and generate pending submissions accordingly
- FR-5.3: Each assessor shall have a running audit count used for POST_N_AUDITS evaluation

### FR-6: Integration
- FR-6.1: External portals shall be able to push assessment-complete events to the PMS
- FR-6.2: The PMS shall expose a status check endpoint that portals can poll before advancing workflow
- FR-6.3: Role and event translation between external portal terminology and PMS terminology shall be configurable per portal per board (PortalAdapter)
- FR-6.4: All integration events shall be logged with direction, status, raw payload, and translated payload

### FR-7: Sync
- FR-7.1: Assessor data shall be syncable in bulk from external systems using employee_id as the idempotent key
- FR-7.2: Admin user accounts shall be syncable in bulk using email as the idempotent key
- FR-7.3: Sync shall be idempotent — repeated calls with the same data produce no side effects
- FR-7.4: New users created via sync shall receive an auto-generated temporary password returned in the sync response (displayed once)
- FR-7.5: Sync shall never overwrite an existing user's password

### FR-8: Audit & Traceability
- FR-8.1: All configuration changes shall be logged as SYSTEM-direction audit log entries
- FR-8.2: All inbound portal events shall be logged as INBOUND entries
- FR-8.3: System admins shall be able to view the global audit log filtered by board, direction, and status

---

## 6. Non-Functional Requirements

| Category | Requirement |
|---|---|
| Security | Passwords stored as bcrypt hashes; JWTs signed with HS256; board data isolation enforced at API layer |
| Performance | Board Comparison Matrix loads via parallel fetches (Promise.allSettled) — one failing board must not block others |
| Availability | Local dev: SQLite; production target: PostgreSQL with persistent storage |
| Maintainability | Board-specific config stored in JSON columns — adding a new board requires no schema change |
| Scalability | Sync endpoints designed for hundreds of assessors per call (bulk upsert pattern) |
| Usability | All forms accessible without login for assessors; no mobile app required in v1 |
| Dark mode | UI supports full dark mode via Tailwind CSS dark: classes |

---

## 7. Boards & Seed Data

| Board | Rating Engine | Forms | Roles |
|---|---|---|---|
| NABL | Numeric (1–5) | 5 (F1_CAB, F2_LEAD, F3_PEER, F4_OFFICER, F5_COMMITTEE) | Lead Assessor, Peer Assessor, Technical Expert, Observer, Officer, Committee Member |
| NABH | Percentage (0–100%) | 4 (F_HCO_PA, F_PEER, F_SECRETARIAT, F_COMMITTEE) | Principal Assessor, Co-Assessor, HCO Rep, Secretariat, Committee Member |
| NABCB | Numeric (1–5) | 1 (F_CAB) | Team Leader, Assessor, Technical Expert, Trainee, Officer, Committee Member |
| NABET | Numeric (1–5) | 1 (F_CLIENT) | Lead Assessor, Assessor, Technical Expert, Officer, Committee Member |

All boards share 5 default Essential Criteria: Professional Ethics, Confidentiality, Impartiality, Integrity, Professional Conduct.

---

## 8. Key Constraints & Decisions

| Decision | Rationale |
|---|---|
| Push model for integration | PMS does not need to know QCI portal's internal API shape; aligns with existing ingest endpoint pattern |
| Token-based public forms (no assessor login) | Reduces friction; assessors are external and should not need accounts |
| Soft-delete for assessors and users | Preserve historical scoring data; never hard-delete |
| JSON config per board | Avoids sparse columns; allows adding new boards without schema migration |
| SQLite for dev / PostgreSQL for prod | Zero-setup local development; production-grade persistence via env var switch |

---

## 9. Future Considerations

- Assessor self-service portal (view own scores, download certificates)
- Outbound webhook delivery (infrastructure registered, not yet dispatched)
- Email notifications to assessors when forms are distributed
- Role-based visibility within a board (e.g., AMC vs Secretariat)
- Analytics/reporting dashboard (trend charts, board-level aggregates)
- Multi-tenancy beyond the 4 QCI boards

---

## 10. Glossary

| Term | Definition |
|---|---|
| AMC | Assessment Management Committee — the administrative body per board |
| Assessor | External domain expert who conducts accreditation assessments |
| Evaluee | The assessor being evaluated in a given form/assessment |
| Evaluator | The person filling in the evaluation form about the evaluee |
| Essential Criterion | A mandatory YES/NO gate on a form; any "No" triggers a review flag |
| Cumulative Rating | Rolling average of an assessor's last N audit scores |
| Star Rating | 1–5 star summary derived from a score using board-configured bands |
| PortalAdapter | Translation layer mapping an external portal's role/event IDs to PMS internal IDs |
| Submission Token | UUID embedded in a public form link; authenticates without requiring login |
| Frequency Rule | Configuration that determines when feedback forms are auto-generated |

---

*Document generated from product conversation history and implemented codebase.*
