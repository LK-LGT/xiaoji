"""🔍 **前端 API 调用 ↔ 后端路由 对账脚本**（2026-11-26）。

⚠️ **为什么需要这个**
「假功能」最常见的形态是：前端有一个按钮 / 页面，
调了某个 ``/v1/xxx`` 路径，而后端**根本没有这条路由**（或返回假成功）。
⇒ 本脚本把 ``frontend/src/api/*.ts`` 里所有的 ``http.get/post/put/delete``
路径抽出来，跟 FastAPI 实际注册的路由表逐一比对，
输出**前端调了但后端没有**的清单。

用法::

    python backend/scripts/audit_frontend_api_contract.py
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
API_DIR = ROOT / "frontend" / "src" / "api"

#: 从 ``http.get("/xxx")`` / ``http.post(\`/xxx\`)`` 里抓路径。
_CALL_RE = re.compile(r"http\.(get|post|put|delete|patch)\s*\(\s*[`'\"]([^`'\"]+)[`'\"]")
#: 模板串里的 ``${...}`` 占位。
_PLACEHOLDER_RE = re.compile(r"\$\{[^}]+\}")


def _normalize(path: str) -> str:
    """把路径归一化成可比对的形式。

    - 去掉 ``${...}`` 占位 ⇒ ``/v1/users/${id}`` → ``/v1/users/``。
    - 去掉尾部斜杠。
    - 统一小写（路由本身大小写不敏感）。
    """
    path = _PLACEHOLDER_RE.sub("", path)
    path = path.rstrip("/")
    return path.lower()


def collect_frontend_calls() -> dict[str, set[str]]:
    """收集前端 API 模块里用到的路径 ⇒ { 方法: {路径} }。"""
    calls: dict[str, set[str]] = {}
    for file in sorted(API_DIR.glob("*.ts")):
        text = file.read_text(encoding="utf-8")
        for method, raw in _CALL_RE.findall(text):
            calls.setdefault(method.upper(), set()).add(_normalize(raw))
    return calls


def collect_backend_routes() -> dict[str, set[str]]:
    """收集 FastAPI 实际注册的路由 ⇒ { 方法: {路径} }。"""
    import sys as _sys

    root = str(ROOT)
    if root not in _sys.path:
        _sys.path.insert(0, root)

    from backend.app.main import app

    routes: dict[str, set[str]] = {}
    for route in app.routes:
        path = getattr(route, "path", None)
        methods = getattr(route, "methods", None)
        if not path or not methods:
            continue
        normalized = _normalize(path)
        for method in methods:
            if method in {"HEAD", "OPTIONS"}:
                continue
            routes.setdefault(method.upper(), set()).add(normalized)
    return routes


def _prefix_match(backend_paths: set[str], frontend_path: str) -> bool:
    """判断前端路径是否能命中某个后端路由。

    ⚠️ 前端路径里常常带**参数段**（``/v1/plans/{id}``），
    后端是 ``/v1/plans/{plan_id}`` ⇒ 参数名不同但结构相同
    ⇒ 需要按「段数 + 静态段」比对。
    """
    if frontend_path in backend_paths:
        return True
    front_parts = [p for p in frontend_path.split("/") if p]
    for backend_path in backend_paths:
        back_parts = [p for p in backend_path.split("/") if p]
        if len(front_parts) != len(back_parts):
            continue
        ok = True
        # ⚠️ ``strict=False`` —— 两边长度已在上面判过相等，
        #    这里 ``strict=True`` 也不会抛，但显式写出来更表意清楚
        #    （ruff B905 要求 zip 显式声明 strict 语义）。
        for fp, bp in zip(front_parts, back_parts, strict=False):
            fp_is_param = fp.startswith("{") or fp.endswith("}") or fp == ""
            bp_is_param = bp.startswith("{") or bp.endswith("}")
            if fp_is_param and bp_is_param:
                continue
            if fp != bp:
                ok = False
                break
        if ok:
            return True
    return False


def main() -> int:
    frontend = collect_frontend_calls()
    backend = collect_backend_routes()

    missing: list[dict[str, str]] = []
    for method, paths in sorted(frontend.items()):
        for path in sorted(paths):
            if not path.startswith(("/v1", "/api")):
                continue
            if not _prefix_match(backend.get(method, set()), path):
                missing.append({"method": method, "path": path})

    print("=" * 72)
    print("前端调用了但后端【没有对应路由】的清单")
    print("=" * 72)
    if not missing:
        print("✅ 没有发现 —— 前端所有 /v1、/api 调用都能命中后端路由。")
    else:
        for item in missing:
            print(f"  ❌ {item['method']:6s} {item['path']}")
        print(f"\n合计：{len(missing)} 条")

    report = ROOT / "backend" / "scripts" / "_frontend_api_audit.json"
    report.write_text(
        json.dumps({"missing": missing}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"\n报告已写入：{report}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
