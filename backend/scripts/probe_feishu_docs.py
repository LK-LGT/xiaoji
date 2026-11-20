"""实测飞书云文档权限现状（只读探针，不写任何数据）。

背景
----
飞书应用的可用 scope 会随 IT 审批而变。本脚本**真实调一次**每个接口，
把「能不能用」变成可验证的事实，而不是靠猜。

用法::

    python backend/scripts/probe_feishu_docs.py
    python backend/scripts/probe_feishu_docs.py <wiki_node_token>
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.app.services.feishu_docs import (  # noqa: E402
    DocsContentUnavailable,
    DocsPermissionError,
    build_docs_client,
)


def probe(docs, label: str, fn) -> tuple[str, str]:
    """跑一个探针，返回 ``(状态, 说明)``。"""
    try:
        detail = fn()
    except DocsPermissionError as exc:
        return ("DENIED", f"缺应用身份权限：{exc.message}")
    except DocsContentUnavailable as exc:
        return ("UNAVAILABLE", str(exc.message))
    except Exception as exc:  # pragma: no cover
        return ("ERROR", f"{type(exc).__name__}: {exc}")
    return ("OK", detail)


def main() -> int:
    token = sys.argv[1] if len(sys.argv) > 1 else ""
    try:
        docs = build_docs_client()
    except Exception as exc:
        print(f"❌ 无法构造飞书客户端：{exc}")
        print("   请检查 FEISHU_APP_ID / FEISHU_APP_SECRET 是否配置。")
        return 2

    try:
        print("=" * 68)
        print("飞书云文档权限探针")
        print("=" * 68)

        if not token:
            print(
                "\n⚠️  未提供 wiki 节点 token —— 只探测「无 token 时的响应」。\n"
                "    用法：python backend/scripts/probe_feishu_docs.py <wiki_node_token>"
            )
            return 1

        # ── 1. 节点元信息（变更检测的基石）──
        status, detail = probe(docs, "get_node", lambda: docs.get_node(token))
        print(f"\n[1] wiki get_node                    → {status}")
        print(f"    {detail}")
        if status == "OK":
            node = docs.get_node(token)
            print(f"    obj_token = {node.get('obj_token')}")
            print(f"    obj_type  = {node.get('obj_type')}")
            print(f"    edit_time = {node.get('edit_time')}   ← 变更检测依据")
            obj_token = node.get("obj_token") or ""
            obj_type = node.get("obj_type") or ""

            # ── 2. 正文抓取 ──
            if obj_token:
                status2, detail2 = probe(
                    docs,
                    "fetch",
                    lambda: f"{len(docs.fetch_document_bytes(obj_token, obj_type)[0])} 字节",
                )
                print(f"\n[2] 正文抓取                          → {status2}")
                print(f"    {detail2}")
                if status2 == "DENIED":
                    print(
                        "    ⇒ 需向 IT 申请：docx:document:readonly / drive:drive:readonly\n"
                        "      / drive:export:readonly / sheets:spreadsheet:readonly"
                    )
        print("\n" + "=" * 68)
        return 0
    finally:
        try:
            docs.client.close()
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
