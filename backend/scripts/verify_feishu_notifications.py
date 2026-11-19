"""经明确授权后，向一名测试员工发送一条 N3–N9 验收通知。"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT / "backend")


def main() -> None:
    from dotenv import dotenv_values
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    env = dotenv_values(".env")
    os.environ["DATABASE_URL"] = env["DATABASE_URL"]
    from backend.app.core.config import settings
    from backend.app.domain.models import AssessmentRecord, AssessmentTask, Base, Employee
    from backend.app.domain.notification_models import Notification
    from backend.app.services.feishu_client import FeishuClient
    from backend.app.services.notification_service import dispatch_pending, enqueue

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--employee-id", required=True)
    parser.add_argument("--scene", required=True, choices=tuple(f"N{i}" for i in range(3, 10)))
    parser.add_argument("--send", required=True, action="store_true", help="确认发送一条真实飞书消息")
    args = parser.parse_args()
    real_engine = create_engine(env["DATABASE_URL"])
    with Session(real_engine) as live:
        person = live.get(Employee, args.employee_id)
        if not person or person.account_status != "enabled" or person.employment_status != "active":
            raise SystemExit("测试员工不存在或已停用")
        identity = person.feishu_open_id
        if not identity:
            raise SystemExit("测试员工缺少 open_id")
        employee_name = person.name
    run = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = ROOT / "backend" / "data" / f"fs4-verification-{run}.db"
    engine = create_engine(f"sqlite:///{path.as_posix()}")
    Base.metadata.create_all(engine)
    receipts = []

    class ReceiptClient:
        def __init__(self):
            self.inner = FeishuClient(app_id=settings.feishu_app_id, app_secret=settings.feishu_app_secret, tenant_id=settings.feishu_tenant_id)

        def send_text_message(self, receive_id, content, **kwargs):
            response = self.inner.send_text_message(receive_id, content, **kwargs)
            receipts.append(response.get("message_id") or (response.get("data") or {}).get("message_id"))
            return response

    client = ReceiptClient()
    try:
        with Session(engine) as db:
            db.add(Employee(id=args.employee_id, name=employee_name, feishu_open_id=identity, employment_status="active", account_status="enabled"))
            db.flush()
            for scene, state in (("N3", "not_started"), ("N4", "self_review_submitted")):
                db.add(AssessmentTask(id=scene, name="验收测试", period="202611", status="created"))
                db.flush()
                db.add(AssessmentRecord(id=scene, task_id=scene, employee_id=args.employee_id, status=state))
            db.flush()
            scene = args.scene
            row = enqueue(db, employee_id=args.employee_id, scene=scene, period=run, object_id=scene, object_type="verification", label=f"【验收测试 {scene}】")
            row.content += "\n这是一条系统验收测试消息，不代表真实绩效状态发生变化。"
            db.commit()
            settings.notification_enabled = True
            settings.notification_canary_employee_id = args.employee_id
            settings.notification_canary_scene = scene
            settings.notification_canary_outbox_id = row.id
            sent = dispatch_pending(db, client=client)
            rows = db.query(Notification).order_by(Notification.scene).all()
            result = {"run": run, "recipient": employee_name, "sent": sent, "receipts": receipts,
                      "scenes": [{"scene": r.scene, "status": r.status, "error": r.last_error} for r in rows],
                      "verification_database": str(path)}
            print(json.dumps(result, ensure_ascii=False))
            if sent != 1 or not all(receipts):
                raise SystemExit("部分场景发送失败或未返回 message_id")
            assert dispatch_pending(db, client=client) == 0
    finally:
        client.inner.close()
        engine.dispose()
        real_engine.dispose()


if __name__ == "__main__":
    main()
