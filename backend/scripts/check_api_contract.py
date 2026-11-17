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
#
# ⚠️ 路径中可能含模板表达式，表达式内部常有括号，例如：
#     `/ai/requests/${encodeURIComponent(requestId)}/cancel`
# 朴素的 `[^)]+` 会在 `encodeURIComponent(` 的左括号处截断，
# 导致 `/ai/requests/${encodeURIComponent(requestId` 被误当成完整路径。
#
# 因此改为「括号配对扫描」：找到 http.<method>( 后，按深度找到匹配的右括号，
# 再从参数区里提取第一个字符串/模板字面量。
#
# ⚠️⚠️ **泛型参数必须用括号配对，不能用 `[^>]*`**（2026-11-16 修复）
#
#    原写法 ``(?:<[^>]*>)?`` 假设泛型里没有嵌套的 `>`，于是：
#      http.get<unknown, PageResponse<WeekPlanSummary>>('/department/week-plans', ...)
#    这个 `[^>]*` 只吃到第一个 `>` 就停了 —— **整个正则匹配不上**，
#    于是这个调用被**静默漏检**：既不进「已对齐」，也不进「缺失」。
#
#    实测漏检 **14 处**（全部是 `PageResponse<X>` / `PageResult<X>` /
#    `Record<string, X>` 这类嵌套泛型），其中一处是真的契约缺口：
#      GET /department/week-plans —— 前端调了，后端**没有**这个端点。
#    修复后前端端点数从 282 → 296，缺失从 0 → 1（缺口被如实暴露）。
#
#    现在改为：先匹配 `http.<method>`，再**按深度**跳过配对的 `<...>`。
HTTP_METHOD_RE = re.compile(r"http\.(get|post|put|delete)\s*", re.S)
HTTP_METHODS = ("GET", "POST", "PUT", "DELETE")


def _skip_generic(text: str, idx: int) -> int:
    """从 ``text[idx] == '<'`` 处按括号配对跳过整个泛型参数。

    支持嵌套：``<unknown, PageResponse<WeekPlanSummary>>``。

    Returns:
        泛型结束后的下标（即 `>` 的后一位）。若未配对闭合，返回原下标。
    """
    n = len(text)
    depth = 0
    i = idx
    while i < n:
        ch = text[i]
        if ch == "<":
            depth += 1
        elif ch == ">":
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return idx


def _find_call_paren(text: str, after_method: int) -> int | None:
    """定位 ``http.<method><泛型>(`` 里的左括号下标。

    Returns:
        左括号下标；找不到返回 None。
    """
    i = after_method
    n = len(text)
    # 跳过空白
    while i < n and text[i].isspace():
        i += 1
    # 跳过泛型参数（可能嵌套）
    if i < n and text[i] == "<":
        i = _skip_generic(text, i)
        while i < n and text[i].isspace():
            i += 1
    if i < n and text[i] == "(":
        return i
    return None


def _extract_path_after_call(text: str, open_paren_idx: int) -> str | None:
    """从 http.<method>( 的左括号开始，取出第一个路径字面量。

    返回归一化前的原始路径片段；找不到则返回 None。
    """
    depth = 0
    i = open_paren_idx
    n = len(text)
    while i < n:
        ch = text[i]
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
            if depth == 0:
                return None
        elif depth == 1:
            # 只在参数区的第一层找路径字面量
            if ch in "`'\"":
                quote = ch
                j = i + 1
                buf: list[str] = []
                while j < n:
                    c = text[j]
                    if c == "\\":
                        j += 2
                        continue
                    if c == quote:
                        break
                    buf.append(c)
                    j += 1
                if j >= n:
                    return None
                return "".join(buf)
        i += 1
    return None


def collect_frontend_endpoints() -> set[tuple[str, str]]:
    """从 frontend/src/api/*.ts 提取调用的方法+路径。"""
    endpoints: set[tuple[str, str]] = set()
    if not FRONTEND_API_DIR.exists():
        return endpoints
    for path in sorted(FRONTEND_API_DIR.rglob("*.ts")):
        text = path.read_text(encoding="utf-8", errors="ignore")
        for match in HTTP_METHOD_RE.finditer(text):
            method = match.group(1).upper()
            # ⚠️ 泛型参数要用**括号配对**跳过（可能嵌套 `>`），
            #    否则 `http.get<unknown, PageResponse<X>>(...)` 整条会被漏检。
            open_paren = _find_call_paren(text, match.end())
            if open_paren is None:
                continue
            raw = _extract_path_after_call(text, open_paren)
            if raw is None:
                continue
            raw = raw.split("?")[0]
            if not raw.startswith("/"):
                continue
            endpoints.add((method, normalize_path(raw)))
    return endpoints


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
