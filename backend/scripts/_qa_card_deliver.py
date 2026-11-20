"""真机投递一张问答卡到飞书（验证 table 组件是否真能渲染）。

⚠️ **为什么必须真机验证**
------------------------
卡片 JSON 写错时，飞书**不会报错** —— 接口返回 200，
但用户看到的是空白卡片或渲染异常的表格。

⇒ 只能靠「发出去 + 肉眼看」确认。

用法::

    python backend/scripts/_qa_card_deliver.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "backend"))

import httpx  # noqa: E402

from backend.app.core.config import settings  # noqa: E402
from backend.app.services import feishu_card  # noqa: E402


def main() -> int:
    print("=" * 74)
    print("真机投递问答卡")
    print("=" * 74)

    # ── 1. 取真实回答 ────────────────────────────────────────
    token = httpx.post(
        "http://127.0.0.1:8000/v1/auth/login",
        json={"username": "employee", "password": "employee123"},
        timeout=15.0,
    ).json()["data"]["accessToken"]

    tasks = httpx.get(
        "http://127.0.0.1:8000/v1/qa/tasks",
        headers={"Authorization": f"Bearer {token}"},
        timeout=15.0,
    ).json()["data"]["items"]
    task_id = tasks[0]["task_id"]

    body = httpx.post(
        "http://127.0.0.1:8000/v1/qa/ask",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "question": "我这个月的分数是怎么算出来的？每项指标各贡献多少分？",
            "task_id": task_id,
        },
        timeout=120.0,
    ).json()
    answer = body.get("answer", "")
    print(f"\n回答长度：{len(answer)} 字")

    # ── 2. 渲染成卡片 ────────────────────────────────────────
    card = feishu_card.build_qa_ai_card(
        answer=answer,
        sources=body.get("sources") or [],
        citation_count=len(body.get("citations") or []),
        period="2026-11",
    )
    elements = card["body"]["elements"]
    print(f"元素序列：{[e.get('tag') for e in elements]}")

    for element in elements:
        if element.get("tag") == "table":
            print(f"\n表头（display_name）："
                  f"{[c['display_name'] for c in element['columns']]}")
            print(f"page_size={element['page_size']}  "
                  f"行数={len(element['rows'])}")
            for row in element["rows"][:3]:
                print(f"  {row}")
            break

    # ── 3. 找到收件人 ────────────────────────────────────────
    # 用最近一次飞书会话的 open_id —— 员工跟机器人聊过天就有。
    from backend.app.core.db import get_sessionmaker
    from backend.app.domain.bot_models import BotSession

    with get_sessionmaker()() as db:
        sessions = (
            db.query(BotSession)
            .filter(BotSession.employee_id.isnot(None))
            .order_by(BotSession.updated_at.desc())
            .all()
        )
        if not sessions:
            print("\n✗ 没有已绑定的飞书会话，无法投递")
            print("   请先在飞书里跟机器人说一句话")
            return 1
        open_id = sessions[0].open_id
        print(f"\n收件人 open_id：{open_id}")

    # ── 4. 投递 ──────────────────────────────────────────────
    from backend.app.services.feishu_client import FeishuClient

    client = FeishuClient(
        app_id=settings.feishu_app_id,
        app_secret=settings.feishu_app_secret,
    )
    result = client.send_card_message(receive_id=open_id, card=card)
    print(f"\n投递结果：{result}")

    print("\n" + "=" * 74)
    print("请在飞书里查看收到的卡片 —— 表格应该是**真表格**（有表头底色）")
    print("=" * 74)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
