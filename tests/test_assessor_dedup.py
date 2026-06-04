"""
Tests for Fix 6 — Assessor Deduplication (Composite Unique Constraint).

Covers:
  - Same employee_id on two different boards → both succeed (200)
  - Same employee_id on same board → second create returns 409
  - Sync with same employee_id across two boards → two separate rows created
  - Sync duplicate employee_id on same board → updates existing (no new row)
"""
import uuid
import pytest

from tests.conftest import make_board, make_user, auth_headers
from app.models.board import Assessor


def sys_headers(db):
    return auth_headers(make_user(db, role="super_admin"))


class TestAssessorDedup:
    def test_same_employee_id_different_boards_succeeds(self, client, db):
        """The same employee_id must be allowed on two different boards."""
        b1 = make_board(db, code="NABH")
        b2 = make_board(db, code="NABL")

        payload = {"employee_id": "EMP_SHARED", "name": "Shared Employee", "role_id": "ROLE_LEAD"}

        r1 = client.post(
            f"/api/v1/boards/{b1.id}/assessors",
            json=payload,
            headers=sys_headers(db),
        )
        assert r1.status_code == 200, r1.text

        r2 = client.post(
            f"/api/v1/boards/{b2.id}/assessors",
            json=payload,
            headers=sys_headers(db),
        )
        assert r2.status_code == 200, r2.text

        # Confirm two distinct rows in DB
        rows = db.query(Assessor).filter_by(employee_id="EMP_SHARED").all()
        assert len(rows) == 2
        board_ids = {r.board_id for r in rows}
        assert b1.id in board_ids
        assert b2.id in board_ids

    def test_same_employee_id_same_board_returns_409(self, client, db):
        """Creating the same employee_id twice on the same board must return 409."""
        board = make_board(db)
        payload = {"employee_id": "EMP_DUP", "name": "First Employee", "role_id": "ROLE_LEAD"}

        r1 = client.post(
            f"/api/v1/boards/{board.id}/assessors",
            json=payload,
            headers=sys_headers(db),
        )
        assert r1.status_code == 200

        r2 = client.post(
            f"/api/v1/boards/{board.id}/assessors",
            json={"employee_id": "EMP_DUP", "name": "Duplicate Employee", "role_id": "ROLE_LEAD"},
            headers=sys_headers(db),
        )
        assert r2.status_code == 409
        assert "EMP_DUP" in r2.json()["detail"] or "already exists" in r2.json()["detail"].lower()

    def test_sync_same_employee_across_two_boards_creates_two_rows(self, client, db):
        """Sync endpoint should create separate assessor rows for the same employee_id on different boards."""
        b1 = make_board(db, code="SYNC1")
        b2 = make_board(db, code="SYNC2")

        sync_payload = {
            "assessors": [
                {"employee_id": "SYNC_EMP", "name": "Sync Employee", "role_id": "ROLE_LEAD"},
            ]
        }

        r1 = client.post(
            f"/api/v1/sync/boards/{b1.id}/assessors",
            json=sync_payload,
            headers=sys_headers(db),
        )
        assert r1.status_code == 200, r1.text

        r2 = client.post(
            f"/api/v1/sync/boards/{b2.id}/assessors",
            json=sync_payload,
            headers=sys_headers(db),
        )
        assert r2.status_code == 200, r2.text

        rows = db.query(Assessor).filter_by(employee_id="SYNC_EMP").all()
        assert len(rows) == 2
        board_ids = {r.board_id for r in rows}
        assert b1.id in board_ids
        assert b2.id in board_ids

    def test_sync_duplicate_on_same_board_updates_not_duplicates(self, client, db):
        """Syncing the same employee_id twice on the same board should update, not create a duplicate."""
        board = make_board(db, code="SYNCDUP")

        r1 = client.post(
            f"/api/v1/sync/boards/{board.id}/assessors",
            json={"assessors": [{"employee_id": "SYNC_DUP_EMP", "name": "Original Name", "role_id": "ROLE_LEAD"}]},
            headers=sys_headers(db),
        )
        assert r1.status_code == 200, r1.text

        # Sync again with updated name
        r2 = client.post(
            f"/api/v1/sync/boards/{board.id}/assessors",
            json={"assessors": [{"employee_id": "SYNC_DUP_EMP", "name": "Updated Name", "role_id": "ROLE_LEAD"}]},
            headers=sys_headers(db),
        )
        assert r2.status_code == 200, r2.text

        # Must be exactly one row — no duplicate
        rows = db.query(Assessor).filter_by(
            employee_id="SYNC_DUP_EMP", board_id=board.id
        ).all()
        assert len(rows) == 1
        assert rows[0].name == "Updated Name"
