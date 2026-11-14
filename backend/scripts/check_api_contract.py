#!/usr/bin/env python
"""前后端 API 契约校验脚本。

比对「前端 `frontend/src/api/*.ts` 的调用」与「后端 FastAPI 实际注册的路由」，
列出缺失项，避免契约脱节静默积累。

用法：
    python backend/scripts/check_api_contract.py                # 人类可读报告
    python backend/scripts/check_api_contract.py --json         # JSON 输出（CI 用）
    python backend/scripts/check_api_contract.py --allow-missing 221   # 白名单放行
    python backend/scripts/check_api_contract.py --strict        # 有缺失即失败

退出码：
    0 = 通过（或缺失数在白名单内）
    1 = 缺失数超出白名单 / --strict 下存在缺失
    2 = 执行异常（如无法加载 app）
"""

from __future__ import annotations

import argparse
import contextlib
import json
import re
import sys
from pathlib import Path

# backend/scripts/ -> backend/ -> 仓库根
BACKEND_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_ROOT.parent
FRONTEND_API_DIR = REPO_ROOT / "frontend" / "src" / "api"

# 前端调用提取：匹配 http.get/post/put/delete<...>('路径'
# 注意：路径中的模板字面量（含三元表达式）会被截断，这里做宽松归一化。
FRONTEND_CALL_RE = re.compile(
    r"http\.(get|post|put|delete)(?:<[^>]*>)?\(\s*[`'\"]?([^`'\"),]+)"
)

HTTP_METHODS = ("GET", "POST", "PUT", "DELETE")


def normalize_path(path: str) -> str:
    """把动态段归一化为 {id}，便于前后端比对。"""
    path = _normalize_template_expressions(path)
    path = re.sub(r"\$\{[^}]+\}", "{id}", path)
    path = re.sub(r"\{[^}]+\}", "{id}", path)
    return path


def _normalize_template_expressions(path: str) -> str:
    """把模板字符串里的 `${...}` 表达式整体替换成 `{id}`。

    前端常用模板字符串 + 三元表达式拼路径，例如：

        `/department/templates/${id}/${enabled ? 'enable' : 'disable'}`

    其中 `${...}` 内部含有 `}`（三元表达式的分支块），用朴素的
    `\\$\\{[^}]+\\}` 会在第一个 `}` 处截断，导致残留 `${enabled ` 这样的
    畸形路径，永远无法与后端的 `/department/templates/{id}/{action}` 匹配。

    这里改为按括号配对扫描，手动找到与 `{` 对应的 `}`，整段替换。
    """
    out: list[str] = []
    i = 0
    n = len(path)
    while i < n:
        # 命中 `${` 起点
        if path.startswith("${", i):
            depth = 1
            j = i + 2
            while j < n and depth > 0:
                if path[j] == "{":
                    depth += 1
                elif path[j] == "}":
                    depth -= 1
                j += 1
            out.append("{id}")
            i = j
            continue
        out.append(path[i])
        i += 1
    return "".join(out)


def collect_backend_endpoints() -> set[tuple[str, str]]:
    """从 FastAPI 的 OpenAPI schema 读取实际注册的路由。

    注意：不能直接遍历 app.routes —— 新版 FastAPI 的 include_router 会把子路由
    包在 _IncludedRouter 里，需展平后才能拿到完整路径。OpenAPI schema 是
    最终对外契约的唯一事实源，直接用它最可靠。
    """
    sys.path.insert(0, str(BACKEND_ROOT))

    # main.py 在 import 时会 print([APP] ...) 注册日志，会污染 --json 的 stdout。
    # 这里临时把 stdout 重定向到 stderr，保证 JSON 输出可被 CI 直接解析。
    with contextlib.redirect_stdout(sys.stderr):
        from app.main import app  # noqa: PLC0415

    endpoints: set[tuple[str, str]] = set()
    for path, operations in app.openapi().get("paths", {}).items():
        # 前端经 Vite 代理把 /api/* 重写成 /v1/*，故去掉 /v1 前缀后比对。
        if path.startswith("/v1/"):
            path = path[3:]
        normalized = normalize_path(path)
        for method in operations:
            if method.upper() in HTTP_METHODS:
                endpoints.add((method.upper(), normalized))
    return endpoints


def collect_frontend_endpoints() -> set[tuple[str, str]]:
    """从 frontend/src/api/*.ts 提取调用的方法+路径。"""
    endpoints: set[tuple[str, str]] = set()
    if not FRONTEND_API_DIR.exists():
        return endpoints
    for path in sorted(FRONTEND_API_DIR.rglob("*.ts")):
        text = path.read_text(encoding="utf-8", errors="ignore")
        for match in FRONTEND_CALL_RE.finditer(text):
            method = match.group(1).upper()
            raw = match.group(2).split("?")[0]
            if not raw.startswith("/"):
                continue
            endpoints.add((method, normalize_path(raw)))
    return endpoints


def group_of(path: str) -> str:
    """按路径前两段聚合业务组，如 /employee/ai/... -> employee/ai。"""
    parts = path.strip("/").split("/")
    return "/".join(parts[:2]) if len(parts) > 1 else parts[0]


def build_report() -> dict:
    backend = collect_backend_endpoints()
    frontend = collect_frontend_endpoints()

    matched = frontend & backend
    missing = sorted(frontend - backend)
    unused = sorted(backend - frontend)

    groups: dict[str, list[str]] = {}
    for method, path in missing:
        groups.setdefault(group_of(path), []).append(f"{method} {path}")

    return {
        "summary": {
            "frontend_endpoints": len(frontend),
            "backend_endpoints": len(backend),
            "matched": len(matched),
            "missing": len(missing),
            "unused": len(unused),
            "missing_groups": len(groups),
        },
        "missing": [f"{m} {p}" for m, p in missing],
        "missing_by_group": {k: sorted(v) for k, v in sorted(groups.items())},
        "unused": [f"{m} {p}" for m, p in unused],
    }


def print_report(report: dict) -> None:
    s = report["summary"]
    print("=" * 66)
    print("前后端 API 契约校验")
    print("=" * 66)
    print(f"  前端调用端点        : {s['frontend_endpoints']}")
    print(f"  后端已注册端点      : {s['backend_endpoints']}")
    print(f"  ✅ 已对齐            : {s['matched']}")
    print(f"  ❌ 前端有后端无      : {s['missing']}  (涉及 {s['missing_groups']} 个业务组)")
    print(f"  ⚠️  后端有前端未用    : {s['unused']}")
    print()

    if report["missing_by_group"]:
        print("-" * 66)
        print("缺失端点（按业务组）")
        print("-" * 66)
        for group, items in report["missing_by_group"].items():
            print(f"\n[{group}]  {len(items)}")
            for item in items:
                print(f"    {item}")
        print()


def main() -> int:
    parser = argparse.ArgumentParser(description="前后端 API 契约校验")
    parser.add_argument("--json", action="store_true", help="以 JSON 输出")
    parser.add_argument(
        "--allow-missing",
        type=int,
        default=None,
        help="允许的缺失端点数量上限；超出则退出码 1",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="只要存在缺失端点即失败（无视白名单）",
    )
    args = parser.parse_args()

    try:
        report = build_report()
    except Exception as exc:  # noqa: BLE001
        print(f"契约校验执行失败: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print_report(report)

    missing_count = report["summary"]["missing"]
    if args.strict and missing_count > 0:
        print(f"❌ 存在 {missing_count} 个缺失端点（--strict）", file=sys.stderr)
        return 1
    if args.allow_missing is not None and missing_count > args.allow_missing:
        print(
            f"❌ 缺失端点 {missing_count} 超出白名单 {args.allow_missing}",
            file=sys.stderr,
        )
        return 1

    # --json 模式下 stdout 必须是纯 JSON，任何额外文本都会破坏 CI 解析。
    if not args.json:
        print(f"✅ 契约校验通过（缺失 {missing_count}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
