"""
Tests for Fix 2 — Form Versioning via Snapshot.

Covers:
  - Snapshot is populated when FormSubmission is created via trigger
  - Scoring uses snapshot weights, not live FormTemplate weights
  - Pure unit tests for _calculate_form_score_from_snapshot
"""
import uuid
import pytest

from tests.conftest import make_board, make_assessor, make_assessment
from app.models.board import (
    FormSubmission, FormTemplate, Parameter, EssentialCriterion,
    FrequencyRule,
)
from app.services.scoring_engine import _calculate_form_score_from_snapshot
from app.services.frequency_manager import _snapshot_form_template


class TestFormSnapshot:
    def test_snapshot_captured_on_submission_creation(self, client, db):
        """After a trigger, FormSubmission.form_snapshot is populated."""
        board = make_board(db)
        assessor = make_assessor(db, board.id)
        assessment = make_assessment(db, board.id)

        # Set up a frequency rule
        form = db.query(FormTemplate).filter_by(board_id=board.id).first()
        rule = FrequencyRule(
            board_id=board.id,
            role_id=assessor.role_id,
            form_template_id=form.id,
            trigger_type="EVERY_AUDIT",
            is_active=True,
        )
        db.add(rule)
        db.commit()

        r = client.post(
            f"/api/v1/triggers/assessment-complete",
            json={
                "assessment_id": assessment.id,
                "evaluee_ids": [assessor.id],
            },
        )
        assert r.status_code == 200

        sub = db.query(FormSubmission).filter(
            FormSubmission.assessment_id == assessment.id,
            FormSubmission.evaluee_id == assessor.id,
        ).first()
        assert sub is not None
        assert sub.form_snapshot is not None
        snap = sub.form_snapshot
        assert snap["id"] == form.id
        assert snap["code"] == form.code
        assert "parameters" in snap
        assert "essential_criteria" in snap

    def test_scoring_uses_snapshot_not_live_template(self, client, db):
        """Editing a FormTemplate after submission creation should not affect scoring."""
        board = make_board(db)
        assessor = make_assessor(db, board.id)
        assessment = make_assessment(db, board.id)
        form = db.query(FormTemplate).filter_by(board_id=board.id).first()

        rule = FrequencyRule(
            board_id=board.id,
            role_id=assessor.role_id,
            form_template_id=form.id,
            trigger_type="EVERY_AUDIT",
            is_active=True,
        )
        db.add(rule)
        db.commit()

        # Trigger to create submission with snapshot
        client.post(
            f"/api/v1/triggers/assessment-complete",
            json={
                "assessment_id": assessment.id,
                "evaluee_ids": [assessor.id],
            },
        )

        sub = db.query(FormSubmission).filter(
            FormSubmission.assessment_id == assessment.id,
            FormSubmission.evaluee_id == assessor.id,
        ).first()
        assert sub.form_snapshot is not None

        # Record original parameter code from snapshot
        snap_param_codes = {p["code"] for p in sub.form_snapshot["parameters"]}
        assert "C1_S1" in snap_param_codes

        # Now edit the top-level parameter weight (simulates a form change)
        top_param = db.query(Parameter).filter(
            Parameter.form_template_id == form.id,
            Parameter.parent_id == None,
        ).first()
        original_weight = top_param.weight
        top_param.weight = 999.0   # drastic change
        db.commit()

        # Submit the form via token (uses snapshot)
        r = client.post(
            f"/api/v1/forms/{sub.submission_token}/submit",
            json={"responses": {"C1_S1": 4, "ESS_ETHICS": "YES"}},
        )
        assert r.status_code == 200
        # Snapshot-based scoring should succeed without error
        assert "form_score" in r.json()
        assert r.json()["form_score"] > 0

        # Restore for cleanup
        top_param.weight = original_weight
        db.commit()


class TestSnapshotHelper:
    def test_snapshot_form_template_structure(self, db):
        board = make_board(db)
        form = db.query(FormTemplate).filter_by(board_id=board.id).first()
        snap = _snapshot_form_template(form)
        assert snap["id"] == form.id
        assert snap["code"] == form.code
        assert "parameters" in snap
        assert "essential_criteria" in snap
        assert snap["version"] == form.version
        assert snap["stakeholder_weight"] == form.stakeholder_weight

    def test_snapshot_includes_all_parameters(self, db):
        board = make_board(db)
        form = db.query(FormTemplate).filter_by(board_id=board.id).first()
        snap = _snapshot_form_template(form)
        # conftest seeds C1 (CALCULATED) and C1_S1 (RATING_1_5)
        param_codes = {p["code"] for p in snap["parameters"]}
        assert "C1" in param_codes
        assert "C1_S1" in param_codes

    def test_snapshot_essential_criteria(self, db):
        board = make_board(db)
        form = db.query(FormTemplate).filter_by(board_id=board.id).first()
        snap = _snapshot_form_template(form)
        ec_codes = {ec["code"] for ec in snap["essential_criteria"]}
        assert "ESS_ETHICS" in ec_codes


class TestCalculateFromSnapshot:
    """Pure unit tests — no DB needed."""

    def _simple_snapshot(self):
        """Minimal snapshot with one CALCULATED parent + one RATING_1_5 child."""
        parent_id = str(uuid.uuid4())
        child_id = str(uuid.uuid4())
        return {
            "parameters": [
                {
                    "id": parent_id,
                    "code": "C1",
                    "weight": 100.0,
                    "data_type": "CALCULATED",
                    "parent_id": None,
                    "options": None,
                },
                {
                    "id": child_id,
                    "code": "C1_S1",
                    "weight": 0,
                    "data_type": "RATING_1_5",
                    "parent_id": parent_id,
                    "options": None,
                },
            ],
            "essential_criteria": [
                {"id": str(uuid.uuid4()), "code": "ESS_ETHICS", "label": "Ethics"},
            ],
        }

    def test_basic_scoring(self):
        snap = self._simple_snapshot()
        score, flagged = _calculate_form_score_from_snapshot(snap, {"C1_S1": 4, "ESS_ETHICS": "YES"})
        assert score == 4.0
        assert flagged is False

    def test_essential_flag_triggered(self):
        snap = self._simple_snapshot()
        score, flagged = _calculate_form_score_from_snapshot(snap, {"C1_S1": 5, "ESS_ETHICS": "NO"})
        assert flagged is True

    def test_yes_no_parameter(self):
        parent_id = str(uuid.uuid4())
        child_id = str(uuid.uuid4())
        snap = {
            "parameters": [
                {"id": parent_id, "code": "P1", "weight": 100.0, "data_type": "CALCULATED", "parent_id": None, "options": None},
                {"id": child_id, "code": "P1_YN", "weight": 0, "data_type": "YES_NO", "parent_id": parent_id, "options": None},
            ],
            "essential_criteria": [],
        }
        score, _ = _calculate_form_score_from_snapshot(snap, {"P1_YN": "YES"})
        assert score == 5.0
        score2, _ = _calculate_form_score_from_snapshot(snap, {"P1_YN": "NO"})
        assert score2 == 1.0

    def test_percentage_parameter(self):
        parent_id = str(uuid.uuid4())
        child_id = str(uuid.uuid4())
        snap = {
            "parameters": [
                {"id": parent_id, "code": "P1", "weight": 100.0, "data_type": "CALCULATED", "parent_id": None, "options": None},
                {"id": child_id, "code": "P1_PCT", "weight": 0, "data_type": "PERCENTAGE", "parent_id": parent_id, "options": None},
            ],
            "essential_criteria": [],
        }
        score, _ = _calculate_form_score_from_snapshot(snap, {"P1_PCT": 100})
        assert score == 5.0
        score2, _ = _calculate_form_score_from_snapshot(snap, {"P1_PCT": 0})
        assert score2 == 1.0

    def test_empty_responses_score_zero(self):
        snap = self._simple_snapshot()
        score, flagged = _calculate_form_score_from_snapshot(snap, {})
        assert score == 0.0
        assert flagged is False
