"""实测飞书云文档权限现状（只读探针，不写任何数据）。

背景
----
飞书应用的可用 scope 会随 IT 审批而变。本脚本**真实调一次**每个接口，
把「能不能用」变成可验证的事实，而不是靠猜。

用法::

    python backend/scripts/probe_feishu_docs.py
    python backend/scripts/probe_feishu_docs.py <wiki_node_token>

⚠️ **权限清单核对**（不消耗接口配额，最快）
    先跑无参模式 —— 它直接读应用已授予的 scope 清单，
    一眼看出缺哪几个。这比逐个试接口准。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.app.core.config import settings  # noqa: E402
from backend.app.services.feishu_client import FeishuClient  # noqa: E402
from backend.app.services.feishu_docs import (  # noqa: E402
    DocsContentUnavailable,
    DocsPermissionError,
    build_docs_client,
)

#: 自动同步需要的 scope → 说明
REQUIRED_SCOPES = {
    "wiki:node:read": "读 wiki 节点元信息（变更检测）—— 现有应用已授予",
    "docx:document:readonly": "读云文档正文（docx）",
    "wiki:wiki:readonly": "扫描知识空间节点（自动发现）",
    "drive:drive:readonly": "读云空间文件",
    "drive:export:readonly": "导出云文档为标准 docx/xlsx",
    "sheets:spreadsheet:readonly": "读电子表格",
}


def probe(fn) -> tuple[str, str]:
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


def check_scopes() -> int:
    """列出已授予 scope，标出还缺哪几个。"""
    print("=" * 68)
    print("飞书应用已授予权限核对")
    print("=" * 68)
    client = FeishuClient(
        app_id=settings.feishu_app_id,
        app_secret=settings.feishu_app_secret,
        tenant_id=settings.feishu_tenant_id,
    )
    try:
        data = client._request("GET", "/application/v6/scopes")
    except Exception as exc:
        print(f"❌ 读取 scope 清单失败：{exc}")
        return 2
    finally:
        try:
            client.close()
        except Exception:
            pass

    granted = {s.get("scope_name") for s in (data.get("scopes") or [])}
    granted.discard(None)
    print(f"\n已授予 scope 总数：{len(granted)}\n")
    missing: list[str] = []
    for scope, note in REQUIRED_SCOPES.items():
        if scope in granted:
            print(f"  ✅ [有] {scope}")
        else:
            missing.append(scope)
            print(f"  ❌ [缺] {scope}")
        print(f"          └ {note}")

    if missing:
        print("\n" + "=" * 68)
        print(f"⚠️  还缺 {len(missing)} 个权限，需向 IT 申请：")
        for scope in missing:
            print(f"     · {scope}")
        print(
            "\n⚠️  **申请后必须让 IT「发布应用新版本」** —— \n"
            "    权限开通但未发版，接口仍会返回 99991672。\n"
            "    这是最常见的坑：以为申请了就能用，实际没发版等于没申请。"
        )
    else:
        print("\n✅ 权限齐备 —— 全自动同步可完整跑通。")
    print("=" * 68)
    return 0 if not missing else 1


def main() -> int:
    token = sys.argv[1] if len(sys.argv) > 1 else ""

    # ── 无参：只核对 scope 清单 ──
    if not token:
        rc = check_scopes()
        print(
            "\n提示：加 wiki 节点 token 可进一步实测接口：\n"
            "    python backend/scripts/probe_feishu_docs.py <wiki_node_token>"
        )
        return rc

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

        # ── 1. 节点元信息（变更检测的基石）──
        status, detail = probe(lambda: docs.get_node(token))
        print(f"\n[1] wiki get_node                    → {status}")
        print(f"    {detail}")
        if status != "OK":
            print("\n⚠️  变更检测都不可用 —— 请先确认应用已发布。")
            return 1

        node = docs.get_node(token)
        print(f"    obj_token = {node.get('obj_token')}")
        print(f"    obj_type  = {node.get('obj_type')}")
        print(f"    edit_time = {node.get('edit_time')}   ← 变更检测依据")

        obj_token = node.get("obj_token") or ""
        obj_type = node.get("obj_type") or ""

        # ── 2. 正文抓取 ──
        if obj_token:
            status2, detail2 = probe(
                lambda: f"{len(docs.fetch_document_bytes(obj_token, obj_type)[0])} 字节"
            )
            print(f"\n[2] 正文抓取                          → {status2}")
            print(f"    {detail2}")
            if status2 == "DENIED":
                print(
                    "    ⇒ 需向 IT 申请：docx:document:readonly / drive:drive:readonly\n"
                    "      / drive:export:readonly / sheets:spreadsheet:readonly\n"
                    "      ⚠️  申请后务必让 IT **发布应用新版本**。"
                )

        # ── 3. 知识空间扫描（自动发现）──
        space_id = node.get("space_id") or settings.feishu_scheme_sync_space_id
        if space_id:
            status3, detail3 = probe(
                lambda: f"读到 {len(docs.client._request('GET', f'/wiki/v2/spaces/{space_id}/nodes', params={'page_size': 5}).get('items') or [])} 个节点"
            )
            print(f"\n[3] 知识空间扫描（自动发现）           → {status3}")
            print(f"    {detail3}")

        print("\n" + "=" * 68)
        return 0
    finally:
        try:
            docs.client.close()
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
