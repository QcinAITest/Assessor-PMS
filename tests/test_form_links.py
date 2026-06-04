"""
Tests for Fix 1 — Token Expiry + Evaluator Binding.

Covers:
  - GET  /api/v1/forms/{token}       — 200 for valid, 410 for expired, legacy NULL not blocked
  - POST /api/v1/forms/{token}/submit — 410 for expired token
"""
import uuid
import pytest
from datetime import datetime, timedelta

from tests.conftest import make_board, make_assessor, make_assessment, make_user, auth_headers
from app.models.board import FormSubmission, FormTemplate


def _make_submission(db, board, expired=False, evaluator_email=None):
    """Helper: create a FormSubmission row with (or without) expiry."""
    form = db.query(FormTemplate).filter_by(board_id=board.id).first()
    token = str(uuid.uuid4())
    expires = (
        datetime.utcnow() - timedelta(days=1)   # already expired
        if expired
        else datetime.utcnow() + timedelta(days=30)
    )
    sub = FormSubmission(
        id=str(uuid.uuid4()),
        form_template_id=form.id,
        status="CREATED",
        responses={},
        submission_token=token,
        token_expires_at=expires,
        evaluator_email=evaluator_email,
    )
    db.add(sub)
    db.commit()
    return sub, token


def _make_legacy_submission(db, board):
    """Legacy row — no expiry set (NULL)."""
    form = db.query(FormTemplate).filter_by(board_id=board.id).first()
    token = str(uuid.uuid4())
    sub = FormSubmission(
        id=str(uuid.uuid4()),
        form_template_id=form.id,
        status="CREATED",
        responses={},
        submission_token=token,
        token_expires_at=None,   # legacy: no expiry
    )
    db.add(sub)
    db.commit()
    return sub, token


class TestTokenExpiry:
    def test_valid_token_returns_200(self, client, db):
        board = make_board(db)
        _, token = _make_submission(db, board, expired=False)
        r = client.get(f"/api/v1/forms/{token}")
        assert r.status_code == 200
        assert r.json()["already_submitted"] is False

    def test_expired_token_get_returns_410(self, client, db):
        board = make_board(db)
        _, token = _make_submission(db, board, expired=True)
        r = client.get(f"/api/v1/forms/{token}")
        assert r.status_code == 410
        assert "expired" in r.json()["detail"].lower()

    def test_expired_token_submit_returns_410(self, client, db):
        board = make_board(db)
        _, token = _make_submission(db, board, expired=True)
        r = client.post(f"/api/v1/forms/{token}/submit", json={"responses": {}})
        assert r.status_code == 410

    def test_legacy_null_expiry_not_blocked(self, client, db):
        """Rows without token_expires_at (legacy) must still be accessible."""
        board = make_board(db)
        _, token = _make_legacy_submission(db, board)
        r = client.get(f"/api/v1/forms/{token}")
        assert r.status_code == 200

    def test_unknown_token_returns_404(self, client, db):
        r = client.get(f"/api/v1/forms/{uuid.uuid4()}")
        assert r.status_code == 404

    def test_evaluator_email_stored_on_generate_link(self, client, db):
        board = make_board(db)
        assessor = make_assessor(db, board.id)
        form = db.query(FormTemplate).filter_by(board_id=board.id).first()
        user = make_user(db, role="super_admin")
        headers = auth_headers(user)

        r = client.post(
            f"/api/v1/boards/{board.id}/forms/{form.id}/generate-link",
            json={"evaluator_email": "test@example.com"},
            headers=headers,
        )
        assert r.status_code == 200
        token = r.json()["token"]

        # Verify it's stored on the submission
        sub = db.query(FormSubmission).filter_by(submission_token=token).first()
        assert sub.evaluator_email == "test@example.com"
        assert sub.token_expires_at is not None

    def test_generate_link_includes_expires_at(self, client, db):
        board = make_board(db)
        form = db.query(FormTemplate).filter_by(board_id=board.id).first()
        user = make_user(db, role="super_admin")
        headers = auth_headers(user)

        r = client.post(
            f"/api/v1/boards/{board.id}/forms/{form.id}/generate-link",
            json={},
            headers=headers,
        )
        assert r.status_code == 200
        assert r.json().get("expires_at") is not None
