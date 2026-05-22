"""
Integration tests for Board CRUD API — including the new edit / delete features.

Uses the in-memory SQLite DB set up in conftest.py.
"""
import uuid
import pytest
from tests.conftest import make_board, make_assessor, make_assessment, make_user, auth_headers


# ── helpers ──────────────────────────────────────────────────────────────────
def sys_headers(db):
    return auth_headers(make_user(db, role="super_admin"))


def board_admin_headers(db, board_id):
    return auth_headers(make_user(db, role="board_admin", board_id=board_id))


# ── GET /api/v1/boards ───────────────────────────────────────────────────────
class TestListBoards:
    def test_system_admin_sees_all_boards(self, client, db):
        make_board(db, code="NABL")
        make_board(db, code="NABH")
        r = client.get("/api/v1/boards", headers=sys_headers(db))
        assert r.status_code == 200
        codes = {b["code"] for b in r.json()}
        assert {"NABL", "NABH"}.issubset(codes)

    def test_board_admin_sees_only_own_board(self, client, db):
        b1 = make_board(db, code="NABL")
        make_board(db, code="NABH")
        headers = board_admin_headers(db, b1.id)
        r = client.get("/api/v1/boards", headers=headers)
        assert r.status_code == 200
        codes = [b["code"] for b in r.json()]
        assert codes == ["NABL"]

    def test_unauthenticated_returns_401(self, client, db):
        r = client.get("/api/v1/boards")
        assert r.status_code == 401


# ── POST /api/v1/boards ──────────────────────────────────────────────────────
class TestCreateBoard:
    def test_create_board_success(self, client, db):
        payload = {
            "code": "NEWB",
            "name": "New Test Board",
            "config": {"rating_engine": "numeric", "cumulative_window": 5, "star_bands": []},
        }
        r = client.post("/api/v1/boards", json=payload, headers=sys_headers(db))
        assert r.status_code == 200
        assert r.json()["code"] == "NEWB"

    def test_duplicate_code_returns_400(self, client, db):
        make_board(db, code="NABL")
        payload = {
            "code": "NABL",
            "name": "Duplicate",
            "config": {"rating_engine": "numeric", "cumulative_window": 5, "star_bands": []},
        }
        r = client.post("/api/v1/boards", json=payload, headers=sys_headers(db))
        assert r.status_code == 400

    def test_board_admin_cannot_create_board(self, client, db):
        board = make_board(db)
        payload = {
            "code": "TEST",
            "name": "Test",
            "config": {"rating_engine": "numeric", "cumulative_window": 5, "star_bands": []},
        }
        r = client.post("/api/v1/boards", json=payload, headers=board_admin_headers(db, board.id))
        assert r.status_code == 403


# ── PUT /api/v1/boards/{id} ──────────────────────────────────────────────────
class TestUpdateBoard:
    def test_update_name(self, client, db):
        board = make_board(db, code="NABL")
        payload = {"name": "Updated NABL Name"}
        r = client.put(f"/api/v1/boards/{board.id}", json=payload, headers=sys_headers(db))
        assert r.status_code == 200
        assert r.json()["name"] == "Updated NABL Name"

    def test_change_rating_engine(self, client, db):
        board = make_board(db, code="NABL", rating_engine="numeric")
        new_config = {
            **board.config,
            "rating_engine": "percentage",
            "star_bands": [{"min_pct": 80, "stars": 5}, {"min_pct": 0, "stars": 1}],
        }
        r = client.put(
            f"/api/v1/boards/{board.id}",
            json={"config": new_config},
            headers=sys_headers(db),
        )
        assert r.status_code == 200
        assert r.json()["config"]["rating_engine"] == "percentage"

    def test_change_cumulative_window(self, client, db):
        board = make_board(db)
        new_config = {**board.config, "cumulative_window": 20}
        r = client.put(
            f"/api/v1/boards/{board.id}",
            json={"config": new_config},
            headers=sys_headers(db),
        )
        assert r.status_code == 200
        assert r.json()["config"]["cumulative_window"] == 20

    def test_board_admin_can_update_own_board(self, client, db):
        board = make_board(db)
        r = client.put(
            f"/api/v1/boards/{board.id}",
            json={"name": "Admin Updated"},
            headers=board_admin_headers(db, board.id),
        )
        assert r.status_code == 200

    def test_board_admin_cannot_update_other_board(self, client, db):
        b1 = make_board(db, code="NABL")
        b2 = make_board(db, code="NABH")
        r = client.put(
            f"/api/v1/boards/{b2.id}",
            json={"name": "Hacked"},
            headers=board_admin_headers(db, b1.id),
        )
        assert r.status_code == 403

    def test_update_nonexistent_board_returns_404(self, client, db):
        r = client.put(
            f"/api/v1/boards/{uuid.uuid4()}",
            json={"name": "Ghost"},
            headers=sys_headers(db),
        )
        assert r.status_code == 404


# ── DELETE /api/v1/boards/{id}  ──────────────────────────────────────────────
class TestDeleteBoard:
    def test_deactivate_board_with_no_assessors(self, client, db):
        board = make_board(db)
        r = client.delete(f"/api/v1/boards/{board.id}", headers=sys_headers(db))
        assert r.status_code == 200
        assert r.json()["deactivated"] is True

    def test_deactivated_board_is_inactive(self, client, db):
        board = make_board(db)
        client.delete(f"/api/v1/boards/{board.id}", headers=sys_headers(db))
        r = client.get(f"/api/v1/boards/{board.id}", headers=sys_headers(db))
        assert r.json()["is_active"] is False

    def test_cannot_deactivate_board_with_active_assessors(self, client, db):
        board = make_board(db)
        make_assessor(db, board.id)          # active assessor
        r = client.delete(f"/api/v1/boards/{board.id}", headers=sys_headers(db))
        assert r.status_code == 409
        assert "active assessor" in r.json()["detail"].lower()

    def test_cannot_deactivate_board_with_open_assessments(self, client, db):
        board = make_board(db)
        make_assessment(db, board.id)        # status IN_PROGRESS
        r = client.delete(f"/api/v1/boards/{board.id}", headers=sys_headers(db))
        assert r.status_code == 409

    def test_board_admin_cannot_delete_board(self, client, db):
        board = make_board(db)
        r = client.delete(
            f"/api/v1/boards/{board.id}",
            headers=board_admin_headers(db, board.id),
        )
        assert r.status_code == 403

    def test_delete_nonexistent_board_returns_404(self, client, db):
        r = client.delete(f"/api/v1/boards/{uuid.uuid4()}", headers=sys_headers(db))
        assert r.status_code == 404

    def test_can_delete_after_deactivating_assessors(self, client, db):
        board = make_board(db)
        assessor = make_assessor(db, board.id)
        # Deactivate assessor first
        client.put(
            f"/api/v1/boards/{board.id}/assessors/{assessor.id}",
            json={"is_active": False},
            headers=sys_headers(db),
        )
        r = client.delete(f"/api/v1/boards/{board.id}", headers=sys_headers(db))
        assert r.status_code == 200


# ── Board lookup by CODE (not just UUID) ─────────────────────────────────────
class TestBoardLookupByCode:
    def test_get_board_by_code(self, client, db):
        make_board(db, code="NABL")
        r = client.get("/api/v1/boards/NABL", headers=sys_headers(db))
        assert r.status_code == 200
        assert r.json()["code"] == "NABL"

    def test_update_board_by_code(self, client, db):
        make_board(db, code="NABL")
        r = client.put("/api/v1/boards/NABL", json={"name": "Updated"}, headers=sys_headers(db))
        assert r.status_code == 200


# ── Role management ──────────────────────────────────────────────────────────
class TestRoles:
    def test_add_and_list_role(self, client, db):
        board = make_board(db)
        payload = {"system_role_id": "ROLE_OBSERVER", "display_label": "Observer"}
        r = client.post(f"/api/v1/boards/{board.id}/roles", json=payload, headers=sys_headers(db))
        assert r.status_code == 200
        roles = client.get(f"/api/v1/boards/{board.id}/roles", headers=sys_headers(db)).json()
        role_ids = [ro["system_role_id"] for ro in roles]
        assert "ROLE_OBSERVER" in role_ids

    def test_delete_role(self, client, db):
        board = make_board(db)
        payload = {"system_role_id": "ROLE_TMP", "display_label": "Temp"}
        role = client.post(f"/api/v1/boards/{board.id}/roles", json=payload, headers=sys_headers(db)).json()
        r = client.delete(f"/api/v1/boards/{board.id}/roles/{role['id']}", headers=sys_headers(db))
        assert r.status_code == 200

    def test_update_role_display_label(self, client, db):
        board = make_board(db)
        # Use ROLE_OBSERVER (not ROLE_LEAD which make_board already seeds)
        payload = {"system_role_id": "ROLE_OBSERVER", "display_label": "Observer"}
        role = client.post(f"/api/v1/boards/{board.id}/roles", json=payload, headers=sys_headers(db)).json()
        r = client.put(
            f"/api/v1/boards/{board.id}/roles/{role['id']}",
            json={"system_role_id": "ROLE_OBSERVER", "display_label": "Senior Observer"},
            headers=sys_headers(db),
        )
        assert r.status_code == 200
        assert r.json()["display_label"] == "Senior Observer"
