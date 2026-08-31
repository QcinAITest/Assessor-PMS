import pytest
from httpx import AsyncClient, ASGITransport
from main import app

SAMPLE_PAYLOAD = [
  {
    "id": 879,
    "form": [
      [
        {
          "rating": "poor",
          "comment": "as"
        },
        {
          "rating": "verygood",
          "comment": "asd"
        }
      ]
    ],
    "user_name": "Ms.Jalpa Krishna Kumar Raichura",
    "other_remark": "PA to Ms.Jalpa Krishna Kumar Raichura",
    "role": "Observer",
    "submitted_at": "2026-04-10 14:30:00"
  },
  {
    "id": 797,
    "form": [
      [
        {
          "rating": "good",
          "comment": "asd"
        }
      ]
    ],
    "user_name": "Dr Muthu Bharathi S",
    "other_remark": "PA to Dr Muthu Bharathi S",
    "role": "Assessor",
    "submitted_at": "2026-04-11T10:00:00Z"
  },
  {
    "id": None,
    "form": [
      [
        {
          "rating": "poor",
          "comment": "da"
        }
      ]
    ],
    "user_name": "",
    "other_remark": "PA to HCO",
    "hospital_name": "Sgs Super Speciality Hospital(run By Shree Hari Arogyam Foundation)"
  }
]


@pytest.mark.anyio
async def test_create_raw_submissions_bulk():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        response = await ac.post("/api/v1/raw-submissions", json=SAMPLE_PAYLOAD)
        assert response.status_code == 201
        data = response.json()
        assert data["status"] == "success"
        assert data["saved_count"] == 3
        assert len(data["saved_ids"]) == 3


@pytest.mark.anyio
async def test_get_raw_submissions_and_filter():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        # Seed test items
        await ac.post("/api/v1/raw-submissions", json=SAMPLE_PAYLOAD)

        # 1. Fetch all
        response = await ac.get("/api/v1/raw-submissions")
        assert response.status_code == 200
        res = response.json()
        assert res["total_count"] >= 3

        # 2. Filter by role
        res_role = await ac.get("/api/v1/raw-submissions?role=Observer")
        assert res_role.status_code == 200
        role_items = res_role.json()["items"]
        assert len(role_items) >= 1
        assert role_items[0]["role"] == "Observer"

        # 3. Filter by legacy_id
        res_legacy = await ac.get("/api/v1/raw-submissions?legacy_id=879")
        assert res_legacy.status_code == 200
        legacy_items = res_legacy.json()["items"]
        assert len(legacy_items) >= 1
        assert legacy_items[0]["legacy_id"] == 879
        assert legacy_items[0]["user_name"] == "Ms.Jalpa Krishna Kumar Raichura"
        assert legacy_items[0]["submitted_at"] is not None

        # 4. Filter by hospital_name partial match
        res_hosp = await ac.get("/api/v1/raw-submissions?hospital_name=Super Speciality")
        assert res_hosp.status_code == 200
        hosp_items = res_hosp.json()["items"]
        assert len(hosp_items) >= 1
        assert "Sgs Super Speciality" in hosp_items[0]["hospital_name"]


@pytest.mark.anyio
async def test_get_single_raw_submission_and_update():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        # Create single
        single_payload = {
            "id": 9999,
            "user_name": "Test User",
            "role": "Lead Assessor",
            "form": [[{"rating": "5", "comment": "Excellent"}]],
            "board_code": "NABL"
        }
        create_res = await ac.post("/api/v1/raw-submissions", json=single_payload)
        assert create_res.status_code == 201
        created_id = create_res.json()["saved_ids"][0]

        # Get by id
        get_res = await ac.get(f"/api/v1/raw-submissions/{created_id}")
        assert get_res.status_code == 200
        assert get_res.json()["user_name"] == "Test User"
        assert get_res.json()["board_code"] == "NABL"
        assert get_res.json()["is_processed"] is False

        # Update process flag
        patch_res = await ac.patch(f"/api/v1/raw-submissions/{created_id}", json={"is_processed": True})
        assert patch_res.status_code == 200
        assert patch_res.json()["is_processed"] is True

        # Delete
        del_res = await ac.delete(f"/api/v1/raw-submissions/{created_id}")
        assert del_res.status_code == 200
