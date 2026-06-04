"""
Webhook / Integration service.

Fires HTTP callbacks to registered external systems when events occur.
Webhooks require a secret for HMAC-SHA256 signing on new registrations.
Legacy rows with NULL secret fire unsigned for backward compatibility.
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
    Find all active webhooks for the board+event and fire real HTTP POST requests.
    - Uses httpx with a 5-second timeout per webhook.
    - Failures are caught and logged; they never propagate to the caller.
    - Legacy rows with NULL secret fire without a signature field.
    - Updates last_fired_at and last_response_status on the Webhook row.
    """
    try:
        import httpx
    except ImportError:
        logger.error("[WEBHOOK] httpx not installed — cannot fire webhooks. Run: pip install httpx")
        return []

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
        # Sign only if secret is present (legacy NULL rows fire unsigned)
        if hook.secret:
            envelope["signature"] = sign_payload(envelope, hook.secret)

        dispatch_status = "failed"
        status_code = None
        error_msg = None

        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.post(hook.target_url, json=envelope)
            status_code = resp.status_code
            dispatch_status = "dispatched" if resp.is_success else "failed"
            if not resp.is_success:
                error_msg = f"HTTP {status_code}"
            logger.info(
                f"[WEBHOOK] {event_type} -> {hook.target_url} | "
                f"status={status_code}"
            )
        except Exception as exc:
            error_msg = str(exc)
            logger.error(
                f"[WEBHOOK] {event_type} -> {hook.target_url} | "
                f"error={exc}"
            )

        # Update dispatch metadata on the webhook row
        hook.last_fired_at = datetime.utcnow()
        hook.last_response_status = status_code

        # Persist outbound dispatch to AuditLog
        log_entry = AuditLog(
            board_id=board_id,
            direction="OUTBOUND",
            event_type=event_type,
            portal_id=hook.target_url,
            raw_payload=envelope,
            status=dispatch_status,
            error=error_msg,
        )
        db.add(log_entry)

        results.append({
            "webhook_id": hook.id,
            "target_url": hook.target_url,
            "status": dispatch_status,
            "http_status": status_code,
        })

    if results:
        db.flush()

    return results
