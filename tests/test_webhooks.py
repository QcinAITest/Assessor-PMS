"""
Tests for Fix 3 — Mandatory HMAC + Real HTTP Dispatch.

Covers:
  - WebhookCreate without secret → 422
  - sign_payload produces correct HMAC
  - fire_webhooks: success path, failure path (non-2xx), network error
  - last_fired_at and last_response_status updated on webhook row
  - Legacy NULL-secret webhook fires without signature field
"""
import json
import uuid
import pytest
import hashlib
import hmac as hmac_lib

from tests.conftest import make_board, make_user, auth_headers
from app.models.board import Webhook, AuditLog
from app.services.webhook_service import sign_payload, fire_webhooks


def sys_headers(db):
    return auth_headers(make_user(db, role="super_admin"))


class TestWebhookCreate:
    def test_create_webhook_requires_secret(self, client, db):
        board = make_board(db)
        r = client.post(
            f"/api/v1/boards/{board.id}/webhooks",
            json={"event_type": "SCORE_CALCULATED", "target_url": "https://example.com/wh"},
            headers=sys_headers(db),
        )
        # No secret → 422 Unprocessable Entity (Pydantic validation)
        assert r.status_code == 422

    def test_create_webhook_with_secret_succeeds(self, client, db):
        board = make_board(db)
        r = client.post(
            f"/api/v1/boards/{board.id}/webhooks",
            json={
                "event_type": "SCORE_CALCULATED",
                "target_url": "https://example.com/wh",
                "secret": "my-test-secret",
            },
            headers=sys_headers(db),
        )
        assert r.status_code in (200, 201)


class TestSignPayload:
    def test_sign_payload_hmac_sha256(self):
        secret = "test-secret"
        payload = {"event": "SCORE_CALCULATED", "board_id": "NABH"}
        sig = sign_payload(payload, secret)

        body = json.dumps(payload, sort_keys=True, default=str)
        expected = hmac_lib.new(secret.encode(), body.encode(), hashlib.sha256).hexdigest()
        assert sig == expected

    def test_sign_payload_different_secrets_differ(self):
        payload = {"data": "test"}
        assert sign_payload(payload, "secret1") != sign_payload(payload, "secret2")

    def test_sign_payload_key_order_stable(self):
        p1 = {"b": 2, "a": 1}
        p2 = {"a": 1, "b": 2}
        assert sign_payload(p1, "s") == sign_payload(p2, "s")


class TestFireWebhooks:
    @pytest.mark.anyio
    async def test_fire_webhooks_success(self, db, httpx_mock):
        board = make_board(db)
        hook = Webhook(
            id=str(uuid.uuid4()),
            board_id=board.id,
            event_type="SCORE_CALCULATED",
            target_url="https://portal.example.com/hook",
            secret="super-secret",
            is_active=True,
        )
        db.add(hook)
        db.commit()

        httpx_mock.add_response(url="https://portal.example.com/hook", status_code=200)

        results = await fire_webhooks(db, board.id, "SCORE_CALCULATED", {"score": 4.5})
        db.flush()

        assert len(results) == 1
        assert results[0]["status"] == "dispatched"
        assert results[0]["http_status"] == 200

        db.refresh(hook)
        assert hook.last_fired_at is not None
        assert hook.last_response_status == 200

        log = db.query(AuditLog).filter_by(direction="OUTBOUND", event_type="SCORE_CALCULATED").first()
        assert log is not None
        assert log.status == "dispatched"

    @pytest.mark.anyio
    async def test_fire_webhooks_non_2xx_marks_failed(self, db, httpx_mock):
        board = make_board(db)
        hook = Webhook(
            id=str(uuid.uuid4()),
            board_id=board.id,
            event_type="SCORE_CALCULATED",
            target_url="https://portal.example.com/hook",
            secret="s",
            is_active=True,
        )
        db.add(hook)
        db.commit()

        httpx_mock.add_response(url="https://portal.example.com/hook", status_code=500)

        results = await fire_webhooks(db, board.id, "SCORE_CALCULATED", {})
        assert results[0]["status"] == "failed"
        assert results[0]["http_status"] == 500

        db.refresh(hook)
        assert hook.last_response_status == 500

        log = db.query(AuditLog).filter_by(direction="OUTBOUND").first()
        assert log.status == "failed"

    @pytest.mark.anyio
    async def test_fire_webhooks_network_error(self, db, httpx_mock):
        import httpx as _httpx
        board = make_board(db)
        hook = Webhook(
            id=str(uuid.uuid4()),
            board_id=board.id,
            event_type="FEEDBACK_DUE",
            target_url="https://unreachable.example.com/hook",
            secret="s",
            is_active=True,
        )
        db.add(hook)
        db.commit()

        httpx_mock.add_exception(
            _httpx.ConnectError("Connection refused"),
            url="https://unreachable.example.com/hook",
        )

        results = await fire_webhooks(db, board.id, "FEEDBACK_DUE", {})
        assert results[0]["status"] == "failed"
        assert results[0]["http_status"] is None

        db.refresh(hook)
        assert hook.last_fired_at is not None
        assert hook.last_response_status is None

    @pytest.mark.anyio
    async def test_null_secret_fires_without_signature(self, db, httpx_mock):
        """Legacy webhooks with NULL secret must fire without a 'signature' field."""
        board = make_board(db)
        hook = Webhook(
            id=str(uuid.uuid4()),
            board_id=board.id,
            event_type="ASSESSMENT_CREATED",
            target_url="https://legacy.example.com/hook",
            secret=None,
            is_active=True,
        )
        db.add(hook)
        db.commit()

        received_bodies = []

        def capture(request):
            received_bodies.append(json.loads(request.content))
            import httpx as _httpx
            return _httpx.Response(200)

        httpx_mock.add_callback(capture, url="https://legacy.example.com/hook")

        await fire_webhooks(db, board.id, "ASSESSMENT_CREATED", {"test": 1})

        assert len(received_bodies) == 1
        assert "signature" not in received_bodies[0]

    @pytest.mark.anyio
    async def test_signed_payload_verifiable(self, db, httpx_mock):
        """Receiver should be able to verify the HMAC signature."""
        secret = "verify-me"
        board = make_board(db)
        hook = Webhook(
            id=str(uuid.uuid4()),
            board_id=board.id,
            event_type="SCORE_CALCULATED",
            target_url="https://verify.example.com/hook",
            secret=secret,
            is_active=True,
        )
        db.add(hook)
        db.commit()

        received_bodies = []

        def capture(request):
            received_bodies.append(json.loads(request.content))
            import httpx as _httpx
            return _httpx.Response(200)

        httpx_mock.add_callback(capture, url="https://verify.example.com/hook")

        await fire_webhooks(db, board.id, "SCORE_CALCULATED", {"score": 3.5})

        envelope = received_bodies[0]
        sig = envelope.pop("signature")
        expected = sign_payload(envelope, secret)
        assert sig == expected
