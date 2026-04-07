"""
Webhook / Integration service.

Fires HTTP callbacks to registered external systems when events occur.
This is the integration layer for connecting with QCI assessment portals.
"""
import hashlib
import hmac
import json
import logging
from datetime import datetime
from typing import Optional
from sqlalchemy.orm import Session
from app.models.board import AuditLog, Webhook

logger = logging.getLogger("qci_pms.webhooks")


def sign_payload(payload: dict, secret: str) -> str:
    body = json.dumps(payload, sort_keys=True, default=str)
    return hmac.new(secret.encode(), body.encode(), hashlib.sha256).hexdigest()


async def fire_webhooks(
    db: Session,
    board_id: str,
    event_type: str,
    payload: dict,
):
    """
    Find all active webhooks for the board+event and dispatch.
    In production, this would use a task queue (Celery/RQ). Here we log for demo.
    """
    hooks = (
        db.query(Webhook)
        .filter(
            Webhook.board_id == board_id,
            Webhook.event_type == event_type,
            Webhook.is_active == True,
        )
        .all()
    )

    results = []
    for hook in hooks:
        envelope = {
            "event": event_type,
            "timestamp": datetime.utcnow().isoformat(),
            "board_id": board_id,
            "data": payload,
        }
        if hook.secret:
            envelope["signature"] = sign_payload(envelope, hook.secret)

        logger.info(f"[WEBHOOK] {event_type} -> {hook.target_url} | payload_keys={list(payload.keys())}")

        # Persist outbound dispatch to AuditLog
        log_entry = AuditLog(
            board_id=board_id,
            direction="OUTBOUND",
            event_type=event_type,
            portal_id=hook.target_url,
            raw_payload=envelope,
            status="dispatched",
        )
        db.add(log_entry)

        results.append({
            "webhook_id": hook.id,
            "target_url": hook.target_url,
            "status": "dispatched",
        })

    if results:
        db.flush()

    return results
