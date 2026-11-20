"""飞书 AI 问答闭环验证：选任务 → 点按钮 → 拿到答案。

⚠️ 上一支脚本验证到「机器人给出选择卡」为止。
这支脚本继续往下走：**点按钮 → 拿到真正的答案**。

这是最容易出问题的一环 —— 按钮的回调载荷、身份校验、
question 传递，任何一环断了用户就点不出答案。
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

PASS = 0
FAIL = 0


def check(name: str, condition: bool, detail: str = "") -> None:
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  ✓ {name}")
    else:
        FAIL += 1
        print(f"  ✗ {name}  {detail}")


def main() -> int:
    from sqlalchemy import select

    from backend.app.core.db import get_sessionmaker
    from backend.app.domain.models import Employee
    from backend.app.routes import feishu_bot as bot_route

    run_id = uuid4().hex[:10]
    print("=" * 70)
    print("飞书 AI 问答闭环验证（选任务 → 点按钮 → 拿答案）")
    print("=" * 70)

    sent_cards: list = []

    class FakeClient:
        def send_card_message(self, receive_id, card, **kwargs):
            sent_cards.append((receive_id, card))
            return {}

    original_client = bot_route._client
    bot_route._client = lambda: FakeClient()

    try:
        with get_sessionmaker()() as db:
            bound = db.scalars(
                select(Employee).where(
                    Employee.feishu_open_id.isnot(None),
                    Employee.feishu_open_id != "",
                    Employee.employment_status == "active",
                    Employee.account_status == "enabled",
                ).limit(1)
            ).first()
            if bound is None:
                print("  ! 库里没有已绑定飞书身份的员工 —— 跳过")
                return 0
            open_id = bound.feishu_open_id
            user_id = bound.feishu_user_id or ""
            print(f"  使用已绑定员工：{bound.name} ({open_id})")

            # ── ① 消息事件 → 拿到选择卡 ──────────────────────
            print("\n[1] 消息事件 → 选择卡")
            body = {
                "schema": "2.0",
                "header": {
                    "event_id": f"evt_close_{run_id}",
                    "event_type": "im.message.receive_v1",
                    "token": "t",
                },
                "event": {
                    "sender": {
                        "sender_id": {"open_id": open_id, "user_id": user_id},
                        "sender_type": "user",
                    },
                    "message": {
                        "message_id": f"om_close_{run_id}",
                        "chat_id": "oc_c",
                        "chat_type": "p2p",
                        "message_type": "text",
                        "content": json.dumps(
                            {"text": "我这个月绩效是多少分？"}, ensure_ascii=False,
                        ),
                    },
                },
            }
            result = asyncio.run(bot_route.receive_bot_event_body(db, body))
            check("事件处理成功", result.get("code") == 0, str(result))
            check("发出了卡片", len(sent_cards) == 1, f"sent={len(sent_cards)}")
            if not sent_cards:
                return 1

            card = sent_cards[0][1]
            title = card["header"]["title"]["content"]
            actions = [
                action
                for element in card["body"]["elements"]
                if element.get("tag") == "action"
                for action in element.get("actions", [])
            ]
            if title != "选择考核任务":
                print(f"  · 该员工只有一个任务（标题={title}）—— 走单任务路径")
                texts = [
                    element.get("content")
                    or (element.get("text") or {}).get("content", "")
                    for element in card["body"]["elements"]
                ]
                blob = "\n".join(t for t in texts if isinstance(t, str))
                check("单任务直接给了答案", "分" in blob, blob[:150])
                check("标注了数据来源", "数据来源" in blob, blob[:200])
            else:
                check("选择卡有按钮", len(actions) > 0)
                if not actions:
                    return 1

                # ── ② 点按钮 → 拿到答案 ──────────────────────
                print("\n[2] 点按钮 → 答案")
                first = actions[0]
                value = first["value"]
                check("按钮带 task_id", bool(value.get("task_id")))
                check("按钮带 question", bool(value.get("question")))
                check("按钮带 period", bool(value.get("period")))

                cb_body = {
                    "schema": "2.0",
                    "header": {
                        "event_id": f"evt_cb_{run_id}",
                        "event_type": "card.action.trigger",
                        "token": "t",
                    },
                    "event": {
                        "operator": {"open_id": open_id, "user_id": user_id},
                        "action": {"value": value, "tag": "button"},
                    },
                }
                sent_cards.clear()
                result = asyncio.run(
                    bot_route._handle_card_action(
                        db, FakeClient(), cb_body, event_id=f"evt_cb_{run_id}",
                    )
                )
                check("回调处理成功", result.get("code") == 0, str(result))
                check("发出了答案卡片", len(sent_cards) == 1, f"sent={len(sent_cards)}")
                if sent_cards:
                    answer_card = sent_cards[0][1]
                    texts = [
                        element.get("content")
                        or (element.get("text") or {}).get("content", "")
                        for element in answer_card["body"]["elements"]
                    ]
                    blob = "\n".join(t for t in texts if isinstance(t, str))
                    check("答案卡片标题正确",
                          answer_card["header"]["title"]["content"] == "绩效问答",
                          answer_card["header"]["title"]["content"])
                    check("答案内容非空", len(blob.strip()) > 0)
                    check("答案按纯文本渲染", not any(
                        e.get("tag") == "markdown" for e in answer_card["body"]["elements"]
                    ))
                    print("\n  --- 卡片正文 ---")
                    for line in blob.splitlines()[:12]:
                        print(f"  | {line}")
                    print("  ----------------")
    finally:
        bot_route._client = original_client

    print("\n" + "=" * 70)
    print(f"结果：{PASS} 通过 / {FAIL} 失败")
    print("=" * 70)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
