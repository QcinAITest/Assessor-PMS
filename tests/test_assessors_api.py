"""
Integration tests for Assessor API — covering the performance card endpoints:
  GET /api/v1/assessors/{id}
  GET /api/v1/assessors/{id}/score-history
  GET /api/v1/assessors/{id}/cumulative-rating
  POST /api/v1/assessments/{id}/calculate-score  (feeds the card)

Also tests basic assessor CRUD and the trigger/scoring pipeline end-to-end.
"""
import uuid
import pytest
from datetime import datetime

from tests.conftest import make_board, make_assessor, make_assessment, make_user, auth_headers
from app.models.board import FormSubmission, AuditScore


def sys_headers(db):
    return auth_headers(make_user(db, role="super_admin"))


def board_admin_headers(db, board_id):
    return auth_headers(make_user(db, role="board_admin", board_id=board_id))


# ── Assessor CRUD ─────────────────────────────────────────────────────────────
class TestAssessorCRUD:
    def test_create_assessor(self, client, db):
        board = make_board(db)
        payload = {"employee_id": "EMP001", "name": "Ravi Kumar", "role_id": "ROLE_LEAD"}
        r = client.post(
            f"/api/v1/boards/{board.id}/assessors",
            json=payload, headers=sys_headers(db),
        )
        assert r.status_code == 200
        assert r.json()["name"] == "Ravi Kumar"

    def test_list_assessors(self, client, db):
        board = make_board(db)
        make_assessor(db, board.id)
        make_assessor(db, board.id)
        r = client.get(f"/api/v1/boards/{board.id}/assessors", headers=sys_headers(db))
        assert r.status_code == 200
        data = r.json()
        assert data["total"] == 2
        assert len(data["items"]) == 2

    def test_update_assessor(self, client, db):
        board = make_board(db)
        assessor = make_assessor(db, board.id)
        r = client.put(
            f"/api/v1/boards/{board.id}/assessors/{assessor.id}",
            json={"name": "Updated Name"},
            headers=sys_headers(db),
        )
        assert r.status_code == 200
        assert r.json()["name"] == "Updated Name"

    def test_deactivate_assessor_soft_deletes(self, client, db):
        board = make_board(db)
        assessor = make_assessor(db, board.id)
        r = client.delete(
            f"/api/v1/boards/{board.id}/assessors/{assessor.id}",
            headers=sys_headers(db),
        )
        assert r.status_code == 200
        # Confirm is_active is now False
        r2 = client.get(
            f"/api/v1/boards/{board.id}/assessors",
            headers=sys_headers(db),
        )
        assessor_data = next(a for a in r2.json()["items"] if a["id"] == assessor.id)
        assert assessor_data["is_active"] is False

    def test_create_assessor_wrong_board_returns_403(self, client, db):
        b1 = make_board(db, code="NABL")
        b2 = make_board(db, code="NABH")
        payload = {"employee_id": "EMP999", "name": "Sneaky", "role_id": "ROLE_LEAD"}
        r = client.post(
            f"/api/v1/boards/{b2.id}/assessors",
            json=payload,
            headers=board_admin_headers(db, b1.id),
        )
        assert r.status_code == 403


# ── Performance Card: GET /api/v1/assessors/{id} ─────────────────────────────
class TestAssessorDetail:
    def test_returns_assessor_with_board_and_role_label(self, client, db):
        board = make_board(db)
        assessor = make_assessor(db, board.id, role_id="ROLE_LEAD")
        r = client.get(f"/api/v1/assessors/{assessor.id}")
        assert r.status_code == 200
        data = r.json()
        assert data["name"] == assessor.name
        assert data["board_code"] == board.code
        # conftest seeds "Lead Assessor" display label for ROLE_LEAD
        assert data["role_label"] == "Lead Assessor"

    def test_nonexistent_assessor_returns_404(self, client, db):
        r = client.get(f"/api/v1/assessors/{uuid.uuid4()}")
        assert r.status_code == 404

    def test_rating_engine_included_in_response(self, client, db):
        board = make_board(db, rating_engine="percentage")
        assessor = make_assessor(db, board.id)
        r = client.get(f"/api/v1/assessors/{assessor.id}")
        assert r.json()["rating_engine"] == "percentage"


# ── Score History ─────────────────────────────────────────────────────────────
class TestScoreHistory:
    def _seed_audit_score(self, db, assessor_id, board_id, assessment_id, final_score, essential=False):
        """Directly insert an AuditScore record."""
        s = AuditScore(
            id=str(uuid.uuid4()),
            assessment_id=assessment_id,
            evaluee_id=assessor_id,
            board_id=board_id,
            form_scores={"F_TEST": {"score": final_score, "weight": 1.0}},
            final_score=final_score,
            base_100_score=((final_score - 1) / 4) * 100,
            star_rating=5 if final_score >= 4.5 else 3,
            essential_flag=essential,
            calculated_at=datetime.utcnow(),
        )
        db.add(s)
        db.commit()
        return s

    def test_empty_history_returns_empty_list(self, client, db):
        board = make_board(db)
        assessor = make_assessor(db, board.id)
        r = client.get(f"/api/v1/assessors/{assessor.id}/score-history")
        assert r.status_code == 200
        data = r.json()
        assert data["total"] == 0
        assert data["items"] == []

    def test_history_includes_assessment_context(self, client, db):
        board = make_board(db)
        assessor = make_assessor(db, board.id)
        assessment = make_assessment(db, board.id)
        self._seed_audit_score(db, assessor.id, board.id, assessment.id, 4.2)

        r = client.get(f"/api/v1/assessors/{assessor.id}/score-history")
        assert r.status_code == 200
        items = r.json()["items"]
        assert len(items) == 1
        record = items[0]
        assert record["organization_name"] == "Acme Labs"
        assert record["assessment_type"] == "Initial"
        assert record["final_score"] == 4.2

    def test_history_returned_in_ascending_order(self, client, db):
        """Scores should be ascending (oldest first) for the chart."""
        board = make_board(db)
        assessor = make_assessor(db, board.id)
        for score in [3.0, 3.5, 4.0, 4.5]:
            assessment = make_assessment(db, board.id)
            self._seed_audit_score(db, assessor.id, board.id, assessment.id, score)

        r = client.get(f"/api/v1/assessors/{assessor.id}/score-history")
        scores = [item["final_score"] for item in r.json()["items"]]
        assert scores == sorted(scores), "Score history should be ascending (oldest → newest)"

    def test_essential_flag_propagated(self, client, db):
        board = make_board(db)
        assessor = make_assessor(db, board.id)
        assessment = make_assessment(db, board.id)
        self._seed_audit_score(db, assessor.id, board.id, assessment.id, 2.0, essential=True)

        r = client.get(f"/api/v1/assessors/{assessor.id}/score-history")
        assert r.json()["items"][0]["essential_flag"] is True

    def test_form_scores_included(self, client, db):
        board = make_board(db)
        assessor = make_assessor(db, board.id)
        assessment = make_assessment(db, board.id)
        self._seed_audit_score(db, assessor.id, board.id, assessment.id, 4.0)

        r = client.get(f"/api/v1/assessors/{assessor.id}/score-history")
        assert "form_scores" in r.json()["items"][0]
        assert "F_TEST" in r.json()["items"][0]["form_scores"]

    def test_history_capped_at_50(self, client, db):
        board = make_board(db)
        assessor = make_assessor(db, board.id)
        for _ in range(55):
            assessment = make_assessment(db, board.id)
            self._seed_audit_score(db, assessor.id, board.id, assessment.id, 3.5)

        r = client.get(f"/api/v1/assessors/{assessor.id}/score-history")
        assert len(r.json()["items"]) <= 50


# ── Cumulative Rating ─────────────────────────────────────────────────────────
class TestCumulativeRating:
    def test_no_scores_returns_no_data_message(self, client, db):
        board = make_board(db)
        assessor = make_assessor(db, board.id)
        r = client.get(f"/api/v1/assessors/{assessor.id}/cumulative-rating")
        assert r.status_code == 200
        assert "message" in r.json()

    def test_cumulative_score_is_average(self, client, db):
        board = make_board(db)
        assessor = make_assessor(db, board.id)
        # Seed two assessments with scores 4.0 and 2.0 → expected average 3.0
        for score in [4.0, 2.0]:
            assessment = make_assessment(db, board.id)
            sub = FormSubmission(
                id=str(uuid.uuid4()),
                assessment_id=assessment.id,
                form_template_id=list(db.query(__import__('app.models.board', fromlist=['FormTemplate'])
                    .FormTemplate).filter_by(board_id=board.id).all())[0].id,
                evaluee_id=assessor.id,
                evaluator_id=assessor.id,
                responses={"C1_S1": score, "ESS_ETHICS": "YES"},
                form_score=score,
                essential_flag=False,
                status="SUBMITTED",
                submitted_at=datetime.utcnow(),
            )
            db.add(sub)
            db.commit()
            client.post(
                f"/api/v1/assessments/{assessment.id}/calculate-score?evaluee_id={assessor.id}",
            )

        r = client.get(f"/api/v1/assessors/{assessor.id}/cumulative-rating")
        data = r.json()
        assert "cumulative_score" in data
        assert abs(data["cumulative_score"] - 3.0) < 0.1

    def test_essential_flag_propagated_to_cumulative(self, client, db):
        board = make_board(db)
        assessor = make_assessor(db, board.id)
        assessment = make_assessment(db, board.id)
        form = db.query(__import__('app.models.board', fromlist=['FormTemplate'])
            .FormTemplate).filter_by(board_id=board.id).first()
        sub = FormSubmission(
            id=str(uuid.uuid4()),
            assessment_id=assessment.id,
            form_template_id=form.id,
            evaluee_id=assessor.id,
            evaluator_id=assessor.id,
            responses={"C1_S1": 4, "ESS_ETHICS": "NO"},
            form_score=4.0,
            essential_flag=True,
            status="SUBMITTED",
            submitted_at=datetime.utcnow(),
        )
        db.add(sub)
        db.commit()
        client.post(
            f"/api/v1/assessments/{assessment.id}/calculate-score?evaluee_id={assessor.id}"
        )
        r = client.get(f"/api/v1/assessors/{assessor.id}/cumulative-rating")
        assert r.json().get("has_essential_flags") is True


# ── Full scoring pipeline (form submit → calculate → history) ─────────────────
class TestScoringPipeline:
    def test_full_pipeline_populates_score_history(self, client, db):
        board = make_board(db)
        assessor = make_assessor(db, board.id)
        assessment = make_assessment(db, board.id)
        form = db.query(__import__('app.models.board', fromlist=['FormTemplate'])
            .FormTemplate).filter_by(board_id=board.id).first()

        # Submit form
        sub_r = client.post(
            f"/api/v1/assessments/{assessment.id}/submissions",
            json={
                "form_template_id": form.id,
                "evaluator_id": assessor.id,
                "evaluee_id": assessor.id,
                "responses": {"C1_S1": 5, "ESS_ETHICS": "YES"},
            },
        )
        assert sub_r.status_code == 200
        assert sub_r.json()["form_score"] > 0

        # Calculate audit score
        calc_r = client.post(
            f"/api/v1/assessments/{assessment.id}/calculate-score?evaluee_id={assessor.id}"
        )
        assert calc_r.status_code == 200
        assert calc_r.json()["final_score"] > 0

        # Verify history now has one record
        hist = client.get(f"/api/v1/assessors/{assessor.id}/score-history").json()["items"]
        assert len(hist) == 1
        assert hist[0]["assessment_type"] == "Initial"
        assert hist[0]["organization_name"] == "Acme Labs"

    def test_calculate_score_idempotent(self, client, db):
        """Calling calculate-score twice should update, not duplicate the AuditScore."""
        board = make_board(db)
        assessor = make_assessor(db, board.id)
        assessment = make_assessment(db, board.id)
        form = db.query(__import__('app.models.board', fromlist=['FormTemplate'])
            .FormTemplate).filter_by(board_id=board.id).first()

        client.post(
            f"/api/v1/assessments/{assessment.id}/submissions",
            json={
                "form_template_id": form.id,
                "evaluator_id": assessor.id,
                "evaluee_id": assessor.id,
                "responses": {"C1_S1": 4, "ESS_ETHICS": "YES"},
            },
        )
        client.post(f"/api/v1/assessments/{assessment.id}/calculate-score?evaluee_id={assessor.id}")
        client.post(f"/api/v1/assessments/{assessment.id}/calculate-score?evaluee_id={assessor.id}")

        hist = client.get(f"/api/v1/assessors/{assessor.id}/score-history").json()["items"]
        assert len(hist) == 1, "Duplicate AuditScore records should not be created"
