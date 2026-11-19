"""Run the SDK-authenticated Feishu receiver beside the HTTP backend.

Usage: python backend/scripts/run_feishu_ws.py
HTTP source validation is unchanged; only SDK callbacks enter business handlers.
"""
from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path
from typing import Any

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
# Match the application's backend-relative SQLite path regardless of launch cwd.
os.chdir(ROOT / "backend")
# Prefer the persisted application configuration over stale terminal test variables.

_app_env = dotenv_values(ROOT / "backend" / ".env")
if _app_env.get("DATABASE_URL"):
    os.environ["DATABASE_URL"] = _app_env["DATABASE_URL"]


def dispatch(body: dict[str, Any]) -> dict[str, Any]:
    from backend.app.core.db import get_sessionmaker
    from backend.app.routes.feishu_bot import (
        BOT_EVENT_TYPES,
        extract_event_type,
        receive_bot_event_body,
    )
    from backend.app.services import feishu_approval_sync, feishu_event
    from backend.app.services.org_change_queue import (
        build_dedup_key,
        enqueue_event,
        parse_contact_event,
    )

    event_type = extract_event_type(body)
    with get_sessionmaker()() as db:
        if event_type in BOT_EVENT_TYPES:
            result = receive_bot_event_body(db, body)
        elif event_type.startswith("contact."):
            parsed = parse_contact_event(body)
            if parsed is None:
                return {"code": 0, "message": "Unsupported contact event"}
            kind, resource_type, resource_id, payload = parsed
            event_id = (body.get("header") or {}).get("event_id")
            enqueue_event(db, kind, resource_type, resource_id, payload,
                          dedup_key=build_dedup_key(kind, resource_type, resource_id, event_id=event_id))
            result = {"code": 0}
        elif event_type.startswith("approval.") or event_type == "out_approval":
            event = feishu_event.parse_event(body)
            result = feishu_approval_sync.apply_feishu_approval_event(
                db, plan_type=event.plan_type, plan_id=event.plan_id,
                feishu_status=event.instance_status,
                instance_id=event.instance_code or event.event_id,
                correlation_key=event.correlation_key, approval_code=event.approval_code,
            )
            result = {"code": 0, "data": str(type(result).__name__)}
        else:
            raise ValueError(f"Unsupported event type: {event_type}")
        db.commit()
        logging.info("Processed event type=%s result=%s", event_type, result.get("message", "ok"))
        return result


def main() -> None:
    import lark_oapi as lark
    from lark_oapi.event.callback.model.p2_card_action_trigger import P2CardActionTriggerResponse

    from backend.app.core.config import settings

    if not settings.feishu_app_id or not settings.feishu_app_secret:
        raise SystemExit("Missing FEISHU_APP_ID or FEISHU_APP_SECRET")

    def on_event(event):
        dispatch(json.loads(lark.JSON.marshal(event)))

    def on_card(event):
        result = dispatch(json.loads(lark.JSON.marshal(event)))
        return P2CardActionTriggerResponse({"toast": {"type": "info", "content": result.get("message", "已处理")}})

    builder = lark.EventDispatcherHandler.builder("", "")
    for method in (
        "register_p2_im_message_receive_v1",
        "register_p2_contact_user_created_v3",
        "register_p2_contact_user_updated_v3",
        "register_p2_contact_user_deleted_v3",
        "register_p2_contact_department_created_v3",
        "register_p2_contact_department_updated_v3",
        "register_p2_contact_department_deleted_v3",
        "register_p2_approval_instance_status_changed_v4",
    ):
        builder = getattr(builder, method)(on_event)
    # Opening the bot chat is a subscribed notification, not a form request.
    builder = builder.register_p2_im_chat_access_event_bot_p2p_chat_entered_v1(lambda event: None)
    builder = builder.register_p1_customized_event("out_approval", on_event)
    builder = builder.register_p2_card_action_trigger(on_card)
    client = lark.ws.Client(settings.feishu_app_id, settings.feishu_app_secret,
                            event_handler=builder.build(), log_level=lark.LogLevel.INFO)
    # SDK INFO logs contain signed WebSocket URLs. Keep credentials out of logs.
    logging.getLogger("Lark").setLevel(logging.WARNING)
    logging.info("Starting Feishu business receiver")
    client.start()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
