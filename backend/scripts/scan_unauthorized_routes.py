"""扫描后端路由，找出**没有任何权限校验**的写操作端点（越权面盘点）。

判定口径（保守，避免误报）：
- 端点所在文件里出现了权限校验调用（``has_permission`` / ``_require_admin`` /
  ``_require_hr`` / ``_assert_can_`` / ``build_access_context`` / ``can_access_`` /
  ``PermissionDenied``）→ 视为**有**权限面（哪怕粒度不精确）。
- 文件里**完全没有**这些调用 → 判定为「只校验了登录、未校验角色/权限」。

同时列出该端点是 GET（只读）还是写操作。
"""

from __future__ import annotations

import ast
import pathlib
import re

ROUTES_DIR = pathlib.Path("backend/app/routes")

#: **守卫调用**的识别规则（正则）。
#:
#: ⚠️ **为什么不用「枚举守卫函数名」**（2026-11-27）
#:   早先这里是一份守卫名清单（``_require_admin`` / ``_require_hr`` / …），
#:   但仓库里实际有 **24+** 个守卫函数，且**命名不统一**：
#:       _require_admin / _require_hr / _require_employee_admin / _require_leader
#:       _require_manager / _require_viewer / _require_bot_token / _require_ai_admin
#:       _assert_can_view / _assert_can_manage / _assert_owns_conversation / …
#:
#:   ⇒ 每加一个守卫就要往清单里补一次，
#:      **漏补 ⇒ 该文件的所有端点被误报成「无权限校验」**。
#:   实测就漏了 ``_require_employee_admin``（2026-11-27 加的 P0 权限闸），
#:   导致 7 个**已有**权限闸的端点被误报。
#:
#: ⚠️ **为什么用「命名形状」而不是「穷举」**
#:   本工具的价值在于**盘点越权面** —— 漏报（把有守卫的当成没守卫）
#:   会淹没信号；而守卫函数的命名在本项目里高度一致
#:   （``_require_*`` / ``_assert_*`` / ``can_*`` / ``has_permission``）。
#:   ⇒ 用**前缀 + 关键词**匹配，天然覆盖将来新增的守卫。
#:
#: ⚠️ **为什么必须先剥掉注释与文档串**
#:   注释里大量出现「守卫词」（本文件自己的说明文字就在讲守卫），
#:   不剥掉会把注释当成代码 ⇒ 误判「有守卫」。
GUARD_PATTERN = re.compile(
    r"""
      has_permission
    | PermissionDenied
    | build_access_context
    | access_context
    | require_role
    | can_(?:access|view|write|edit|manage)_        # can_view_ / can_write_ / …
    | _require_[a-z0-9_]+                            # _require_admin / _require_hr / …
    | _assert_[a-z0-9_]+                             # _assert_can_view / _assert_hr / …
    | verify_agent_token
    | _scope_for
    | _approval_scope
    | _owner_in_scope
    """,
    re.VERBOSE,
)


def route_methods(path: pathlib.Path) -> list[tuple[str, str, int]]:
    """返回 [(method, path, lineno)]。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out: list[tuple[str, str, int]] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)):
            continue
        for deco in node.decorator_list:
            if not isinstance(deco, ast.Call):
                continue
            f = deco.func
            if not isinstance(f, ast.Attribute):
                continue
            method = f.attr.upper()
            if method not in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
                continue
            route_path = ""
            if deco.args and isinstance(deco.args[0], ast.Constant):
                route_path = str(deco.args[0].value)
            out.append((method, route_path, node.lineno))
    return out


def main() -> None:
    rows: list[tuple[str, str, str, int]] = []
    for path in sorted(ROUTES_DIR.rglob("*.py")):
        if path.name.startswith("_"):
            continue
        src = path.read_text(encoding="utf-8")
        if "@router" not in src:
            continue
        # 去掉注释与文档串，避免注释里的守卫词造成误判
        code_only = re.sub(r'""".*?"""', "", src, flags=re.S)
        code_only = re.sub(r"#.*", "", code_only)
        has_guard = bool(GUARD_PATTERN.search(code_only))
        if has_guard:
            continue
        for method, route_path, lineno in route_methods(path):
            rows.append((str(path), method, route_path, lineno))

    print(f"⚠️ 文件级无任何权限校验调用的端点：{len(rows)} 个\n")
    by_file: dict[str, list[tuple[str, str, int]]] = {}
    for f, m, p, ln in rows:
        by_file.setdefault(f, []).append((m, p, ln))
    for f, items in by_file.items():
        print(f"── {f}")
        for m, p, ln in sorted(items, key=lambda x: x[2]):
            print(f"   L{ln:<5} {m:<6} {p}")
        print()


if __name__ == "__main__":
    main()
