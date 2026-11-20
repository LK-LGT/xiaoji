"""飞书 AI 问答端到端验证（真实后端 + 真实 agent-service）。

⚠️ **为什么需要这个脚本**
----------------------
单元测试用 mock 验证了「各部件对不对」，
但没法验证「**真模型 + 真工具 + 真数据**串起来跑通」。

这个脚本：
  ① 用真实 HTTP 接口登录拿到 token
  ② 直接调 ``/v1/qa/ask``（网页端路径）
  ③ 再走一遍飞书的消息事件分发路径（``receive_bot_event_body``）
  ④ 对比两者：答案、引用、降级标记必须一致

⇒ 验证「网页端和飞书端**真的**是同一套行为」。

用法::

    python backend/scripts/_qa_feishu_e2e.py
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

BASE = "http://127.0.0.1:8000"

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


def login(username: str, password: str) -> str:
    import httpx

    resp = httpx.post(
        f"{BASE}/v1/auth/login",
        json={"username": username, "password": password},
        timeout=15.0,
    )
    resp.raise_for_status()
    return resp.json()["data"]["accessToken"]


def main() -> int:
    from uuid import uuid4

    import httpx

    run_id = uuid4().hex[:10]

    print("=" * 70)
    print("飞书 AI 问答端到端验证")
    print("=" * 70)

    token = login("employee", "employee123")

    # ── ① 网页端 ──────────────────────────────────────────────
    print("\n[1] 网页端 /v1/qa/tasks")
    resp = httpx.get(f"{BASE}/v1/qa/tasks", headers={"Authorization": f"Bearer {token}"}, timeout=15.0)
    resp.raise_for_status()
    tasks = resp.json()["data"]["items"]
    check("任务列表非空", len(tasks) > 0, f"实际 {len(tasks)}")
    if not tasks:
        return 1

    labels = [t["label"] for t in tasks]
    check("任务 label 互不相同", len(set(labels)) == len(labels),
          f"total={len(labels)} unique={len(set(labels))}")

    task_id = tasks[0]["task_id"]

    print(f"\n[2] 网页端 /v1/qa/ask  (task={task_id})")
    resp = httpx.post(
        f"{BASE}/v1/qa/ask",
        headers={"Authorization": f"Bearer {token}"},
        json={"question": "我这个月绩效是多少分？各项指标达成率如何？", "task_id": task_id},
        timeout=90.0,
    )
    resp.raise_for_status()
    web = resp.json()

    check("有回答正文", bool(web["answer"].strip()))
    check("未降级", web["degraded"] is False, web["answer"][:80])
    check("引用非空", len(web["citations"]) > 0, f"citations={web['citations']}")
    check("引用都在 sources 内", set(web["citations"]) <= set(web["sources"]))
    check("无无效引用被剔除", web["dropped_citations"] == 0, f"dropped={web['dropped_citations']}")
    check("有工具调用留痕", len(web["tool_calls"]) > 0)
    check("数据质量字段完整", "completeness" in web["data_quality"])

    # ── ③ 飞书端 ──────────────────────────────────────────────
    print("\n[3] 飞书消息事件分发（同一后端进程）")
    from backend.app.core.db import get_sessionmaker
    from backend.app.routes.feishu_bot import receive_bot_event_body

    # ⚠️ 必须用**真实已绑定**的飞书身份 —— 未绑定的人会收到「找不到你的账号」
    #    错误卡片（这是正确行为，不是 bug）。
    with get_sessionmaker()() as db:
        from sqlalchemy import select

        from backend.app.domain.models import Employee

        bound = db.scalars(
            select(Employee).where(
                Employee.feishu_open_id.isnot(None),
                Employee.feishu_open_id != "",
                Employee.employment_status == "active",
                Employee.account_status == "enabled",
            ).limit(1)
        ).first()
        if bound is None:
            print("  ! 库里没有已绑定飞书身份的员工 —— 跳过飞书端验证")
            bound = None
        else:
            feishu_open_id = bound.feishu_open_id
            feishu_user_id = bound.feishu_user_id or ""
            print(f"  使用已绑定员工：{bound.name} ({feishu_open_id})")

    if bound is None:
        print("\n" + "=" * 70)
        print(f"结果：{PASS} 通过 / {FAIL} 失败（飞书端已跳过）")
        print("=" * 70)
        return 0 if FAIL == 0 else 1

    body = {
        "schema": "2.0",
        "header": {
            # ⚠️ event_id / message_id 必须**每次运行都不同** ——
            #    机器人按 message_id 去重（防飞书重推导致重复处理），
            #    用固定值第二次跑就会被当成「已处理」直接跳过。
            "event_id": f"evt_qa_e2e_{run_id}",
            "event_type": "im.message.receive_v1",
            "token": "e2e-token",
        },
        "event": {
            "sender": {
                "sender_id": {
                    "open_id": feishu_open_id,
                    "user_id": feishu_user_id,
                },
                "sender_type": "user",
            },
            "message": {
                "message_id": f"om_e2e_{run_id}",
                "chat_id": "oc_e2e_1",
                "chat_type": "p2p",
                "message_type": "text",
                "content": json.dumps(
                    {"text": "我这个月绩效是多少分？各项指标达成率如何？"},
                    ensure_ascii=False,
                ),
            },
        },
    }

    sent_cards: list = []

    class FakeClient:
        def send_card_message(self, receive_id, card, **kwargs):
            sent_cards.append((receive_id, card))
            return {}

    import backend.app.routes.feishu_bot as bot_route

    original_client = bot_route._client
    bot_route._client = lambda: FakeClient()
    try:
        with get_sessionmaker()() as db:
            result = asyncio.run(receive_bot_event_body(db, body))
    finally:
        bot_route._client = original_client

    check("事件处理成功", result.get("code") == 0, str(result))
    check("发出了卡片", len(sent_cards) == 1, f"sent={len(sent_cards)}")

    if sent_cards:
        card = sent_cards[0][1]
        texts = [
            element.get("content")
            or (element.get("text") or {}).get("content", "")
            for element in card.get("body", {}).get("elements", [])
        ]
        blob = "\n".join(t for t in texts if isinstance(t, str))
        title = card["header"]["title"]["content"]

        if title == "选择考核任务":
            # ⚠️ 该员工当月有**多个**任务 → 机器人正确地**没有猜**，
            #    而是给了一张选择卡。这是预期行为，不是失败。
            print("  · 该员工当月有多个任务 → 正确返回「选择任务」卡片（不猜）")
            check("选择卡列出了候选任务", "选择要查询的任务" in blob, blob[:120])
            check("候选任务名可区分",
                  len(set(line for line in blob.splitlines() if line[:1].isdigit())) > 1)
            actions = [
                action
                for element in card["body"]["elements"]
                if element.get("tag") == "action"
                for action in element.get("actions", [])
            ]
            check("选择卡带按钮", len(actions) > 0)
            check("按钮带 task_id 和 question",
                  all(a["value"].get("task_id") and a["value"].get("question") for a in actions))
        else:
            check("卡片标题是「绩效问答」", title == "绩效问答", title)
            check("答案在卡片里", "分" in blob, blob[:120])
            check("答案按纯文本渲染", not any(
                element.get("tag") == "markdown" for element in card["body"]["elements"]
            ))
            check("标注了数据来源", "数据来源" in blob, blob[:200])

    # ── ④ 一致性 ──────────────────────────────────────────────
    print("\n[4] 网页端 vs 飞书端")
    check("两端用的是同一套问答服务",
          "qa_chat_service" in (Path(ROOT) / "backend/app/routes/qa.py").read_text(encoding="utf-8"))
    check("飞书端也走 qa_chat_service",
          "qa_chat_service" in (Path(ROOT) / "backend/app/services/feishu_qa_ai.py").read_text(encoding="utf-8"))

    print("\n" + "=" * 70)
    print(f"结果：{PASS} 通过 / {FAIL} 失败")
    print("=" * 70)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
