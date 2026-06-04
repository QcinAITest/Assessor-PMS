"""
Tests for Fix 5 — Assessment Status Lifecycle.

Valid transitions:
  IN_PROGRESS → PENDING_FEEDBACK  (via trigger-assessment-complete)
  PENDING_FEEDBACK → SCORED       (automatic via calculate-score)
  SCORED → CLOSED                 (manual via PATCH /status)

Invalid transitions must return 422.
"""
import uuid
import pytest
from datetime import datetime

from tests.conftest import make_board, make_assessor, make_assessment, make_user, auth_headers
from app.models.board import FormSubmission, FormTemplate, Assessment, FrequencyRule


def sys_headers(db):
    return auth_headers(make_user(db, role="super_admin"))


def _add_frequency_rule(db, board, assessor, form):
    rule = FrequencyRule(
        board_id=board.id,
        role_id=assessor.role_id,
        form_template_id=form.id,
        trigger_type="EVERY_AUDIT",
        is_active=True,
    )
    db.add(rule)
    db.commit()
    return rule


class TestAssessmentLifecycle:
    def test_initial_status_is_in_progress(self, client, db):
        board = make_board(db)
        assessment = make_assessment(db, board.id)
        assert assessment.status == "IN_PROGRESS"

    def test_trigger_sets_pending_feedback(self, client, db):
        board = make_board(db)
        assessor = make_assessor(db, board.id)
        assessment = make_assessment(db, board.id)

        r = client.post(
            "/api/v1/triggers/assessment-complete",
            json={"assessment_id": assessment.id, "evaluee_ids": [assessor.id]},
        )
        assert r.status_code == 200

        db.refresh(assessment)
        assert assessment.status == "PENDING_FEEDBACK"

    def test_calculate_score_sets_scored(self, client, db):
        board = make_board(db)
        assessor = make_assessor(db, board.id)
        assessment = make_assessment(db, board.id)
        form = db.query(FormTemplate).filter_by(board_id=board.id).first()

        # Manually set to PENDING_FEEDBACK
        assessment.status = "PENDING_FEEDBACK"
        db.commit()

        # Submit a form so scoring has data
        sub = FormSubmission(
            id=str(uuid.uuid4()),
            assessment_id=assessment.id,
            form_template_id=form.id,
            evaluee_id=assessor.id,
            evaluator_id=assessor.id,
            responses={"C1_S1": 4, "ESS_ETHICS": "YES"},
            form_score=4.0,
            essential_flag=False,
            status="SUBMITTED",
            submitted_at=datetime.utcnow(),
        )
        db.add(sub)
        db.commit()

        r = client.post(
            f"/api/v1/assessments/{assessment.id}/calculate-score?evaluee_id={assessor.id}"
        )
        assert r.status_code == 200
        assert r.json()["assessment_status"] == "SCORED"

        db.refresh(assessment)
        assert assessment.status == "SCORED"

    def test_calculate_score_idempotent_on_scored(self, client, db):
        """Calling calculate-score on an already-SCORED assessment keeps it SCORED."""
        board = make_board(db)
        assessor = make_assessor(db, board.id)
        assessment = make_assessment(db, board.id)
        form = db.query(FormTemplate).filter_by(board_id=board.id).first()

        assessment.status = "PENDING_FEEDBACK"
        db.commit()

        sub = FormSubmission(
            id=str(uuid.uuid4()),
            assessment_id=assessment.id,
            form_template_id=form.id,
            evaluee_id=assessor.id,
            evaluator_id=assessor.id,
            responses={"C1_S1": 3, "ESS_ETHICS": "YES"},
            form_score=3.0,
            essential_flag=False,
            status="SUBMITTED",
            submitted_at=datetime.utcnow(),
        )
        db.add(sub)
        db.commit()

        # First call: PENDING_FEEDBACK → SCORED
        client.post(f"/api/v1/assessments/{assessment.id}/calculate-score?evaluee_id={assessor.id}")
        db.refresh(assessment)
        assert assessment.status == "SCORED"

        # Second call: already SCORED — must remain SCORED (not regress)
        r2 = client.post(f"/api/v1/assessments/{assessment.id}/calculate-score?evaluee_id={assessor.id}")
        assert r2.status_code == 200
        db.refresh(assessment)
        assert assessment.status == "SCORED"

    def test_patch_scored_to_closed(self, client, db):
        board = make_board(db)
        assessment = make_assessment(db, board.id)
        assessment.status = "SCORED"
        db.commit()

        r = client.patch(
            f"/api/v1/boards/{board.id}/assessments/{assessment.id}/status",
            json={"new_status": "CLOSED"},
            headers=sys_headers(db),
        )
        assert r.status_code == 200
        assert r.json()["new_status"] == "CLOSED"
        assert r.json()["old_status"] == "SCORED"

        db.refresh(assessment)
        assert assessment.status == "CLOSED"

    def test_invalid_transition_returns_422(self, client, db):
        board = make_board(db)
        assessment = make_assessment(db, board.id)
        # IN_PROGRESS cannot jump to CLOSED directly
        r = client.patch(
            f"/api/v1/boards/{board.id}/assessments/{assessment.id}/status",
            json={"new_status": "CLOSED"},
            headers=sys_headers(db),
        )
        assert r.status_code == 422

    def test_no_backward_transition(self, client, db):
        board = make_board(db)
        assessment = make_assessment(db, board.id)
        assessment.status = "SCORED"
        db.commit()

        r = client.patch(
            f"/api/v1/boards/{board.id}/assessments/{assessment.id}/status",
            json={"new_status": "IN_PROGRESS"},
            headers=sys_headers(db),
        )
        assert r.status_code == 422

    def test_closed_is_terminal(self, client, db):
        board = make_board(db)
        assessment = make_assessment(db, board.id)
        assessment.status = "CLOSED"
        db.commit()

        r = client.patch(
            f"/api/v1/boards/{board.id}/assessments/{assessment.id}/status",
            json={"new_status": "SCORED"},
            headers=sys_headers(db),
        )
        assert r.status_code == 422
        assert "terminal" in r.json()["detail"].lower() or "none" in r.json()["detail"].lower()

    def test_patch_nonexistent_assessment_returns_404(self, client, db):
        board = make_board(db)
        r = client.patch(
            f"/api/v1/boards/{board.id}/assessments/{uuid.uuid4()}/status",
            json={"new_status": "CLOSED"},
            headers=sys_headers(db),
        )
        assert r.status_code == 404
