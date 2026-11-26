#!/usr/bin/env python3
"""把 CI 失败报告发布到**诊断分支**（GitHub REST API）。

⚠️ **为什么需要这个脚本（2026-11-25）**

排查 CI 失败时，三条常规途径**全部**可能拿不到信息：

1. **job log** —— 需要 **admin 权限**才能下载；
2. **artifact** —— 需要**登录态**才能下载；
3. **job summary** —— 只能在网页上看，API 读不到。

⇒ 结果就是「CI 红了，但没人知道为什么红」。

✅ **本脚本的解法**：失败时把失败详情提交到一个**专用分支**，
用普通 ``git fetch`` 就能拉下来（git 凭据是有的）⇒ 任何人都能看。

⚠️ **为什么用 REST API 而不是 ``git push``**
``git push`` 在 CI 里要处理 credential helper / token 注入，
实测推不上去（分支始终不出现）⇒ 改用 API：
``GITHUB_TOKEN`` 自带、权限明确、失败有明确返回码可查。

⚠️ **为什么用「孤儿提交」（orphan commit）而不是在分支上追加**
孤儿提交（``parent = null``）**不依赖**目标分支是否存在
⇒ 第一次失败就能建出分支 ⇒ 不用先手动建分支。

用法::

    python backend/scripts/publish_ci_diagnostics.py \\
        --repo owner/name --branch ci-diagnostics/failure-report \\
        --junit data/_pytest_report.xml --run-id 12345
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import quote

_API = os.environ.get("GITHUB_API_BASE") or "https://api.github.com"


def _request(
    method: str, url: str, token: str, payload: dict
) -> tuple[int, dict]:
    """调 GitHub API。返回 ``(状态码, 响应 JSON)``。

    ⚠️ **永远不抛异常** —— 返回 ``(0, {...})`` 表示网络层失败。
    ⇒ CI 里能拿到明确的错误码，而不是一堆堆栈。
    """
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "Content-Type": "application/json",
            "User-Agent": "ci-diagnostics",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            return resp.status, json.loads(body) if body else {}
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        try:
            return exc.code, json.loads(body)
        except json.JSONDecodeError:
            return exc.code, {"raw": body[:500]}
    except urllib.error.URLError as exc:
        return 0, {"error": str(exc)}


def _post(url: str, token: str, payload: dict) -> tuple[int, dict]:
    """调 GitHub API（POST —— 创建资源）。"""
    return _request("POST", url, token, payload)


def _patch(url: str, token: str, payload: dict) -> tuple[int, dict]:
    """调 GitHub API（PATCH —— 更新已有资源）。"""
    return _request("PATCH", url, token, payload)


def _get(url: str, token: str) -> tuple[int, dict]:
    """调 GitHub API（GET）。返回 ``(状态码, 响应 JSON)``。"""
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "ci-diagnostics",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            return resp.status, json.loads(body) if body else {}
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        try:
            return exc.code, json.loads(body)
        except json.JSONDecodeError:
            return exc.code, {"raw": body[:500]}
    except urllib.error.URLError as exc:
        return 0, {"error": str(exc)}


def _read_failures(junit: Path) -> str:
    """把 junit XML 转成人类可读的失败摘要。"""
    import xml.etree.ElementTree as ET

    if not junit.exists():
        return f"（没有找到 junit 报告：{junit}）\n"

    try:
        root = ET.parse(junit).getroot()
    except ET.ParseError as exc:
        return f"（junit 报告解析失败：{exc}）\n"

    lines: list[str] = []
    count = 0
    for case in root.iter("testcase"):
        for child in case:
            if child.tag not in ("failure", "error"):
                continue
            count += 1
            lines.append("=" * 72)
            lines.append(
                f"FAILED: {case.get('classname')}::{case.get('name')}"
            )
            message = (child.get("message") or "").strip()
            if message:
                lines.append("--- message ---")
                lines.append(message[:2000])
            text = (child.text or "").strip()
            if text:
                lines.append("--- traceback ---")
                lines.append(text[-3000:])

    if not count:
        # ⚠️ **收集阶段失败**（import 错误 / 语法错误）不会有 testcase 节点
        #    ⇒ 这种情况必须提示去看原始输出，否则会以为「没有失败」。
        lines.append("（junit 报告里没有失败用例 —— 失败可能发生在**收集阶段**）")
        lines.append("请查看 pytest 的原始输出（import 错误 / 语法错误）。")

    return "\n".join(lines) + "\n"


def _git_push(args, files: dict[str, str]) -> bool:
    """主通道：直接用 ``git push`` 把报告推到诊断分支。

    ⚠️ **为什么主通道用 git 而不是 REST API**（2026-11-26 实测）
    REST API 那条路在这套环境里**一直没建出分支**（POST 422 / 各种边角），
    而 ``git push`` 是**被验证过能通的**（开发者日常推送走的就是它）。

    ⚠️ **认证从哪来**
    CI 里由 ``actions/checkout`` 配置的 extraheader 提供
    （``GITHUB_TOKEN``）⇒ 不需要额外配 credential。

    ⚠️ **必须用「独立仓库」而不是在 CI 检出的仓库上操作**
    否则会把检出状态搞乱（后续步骤的工作目录/分支就变了）。

    ⚠️ **失败也不阻断主流程** —— 报告发不出去不是测试失败的原因。
    """
    import subprocess
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        for name, content in files.items():
            (tmp_path / name).write_text(content, encoding="utf-8")

        def run(*cmd: str) -> tuple[int, str]:
            proc = subprocess.run(
                cmd,
                cwd=tmp_path,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            return proc.returncode, (proc.stdout or "") + (proc.stderr or "")

        # ⚠️ **先 init 一个独立仓库** ⇒ 不碰 CI 检出的那个仓库。
        code, out = run("git", "init", "-q")
        if code != 0:
            print(f"   git init 失败：{out[:300]}", file=sys.stderr)
            return False
        for cmd in (
            ("git", "config", "user.name", "ci-diagnostics"),
            ("git", "config", "user.email", "ci-diagnostics@localhost"),
            ("git", "add", "-A"),
            ("git", "commit", "-q", "-m", f"ci: 失败报告 run={args.run_id or 'unknown'}"),
        ):
            code, out = run(*cmd)
            if code != 0:
                print(f"   {' '.join(cmd)} 失败：{out[:300]}", file=sys.stderr)
                return False

        remote = os.environ.get("GIT_PUSH_URL") or f"https://github.com/{args.repo}.git"
        # ⚠️ **HEAD:refs/heads/<branch>** —— 明确目标 ref，
        #   避免分支名歧义。
        code, out = run(
            "git", "push", "--force", remote, f"HEAD:refs/heads/{args.branch}"
        )
        if code != 0:
            print(f"   git push 失败：{out[:500]}", file=sys.stderr)
            return False
        return True


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="发布 CI 失败报告到诊断分支")
    parser.add_argument("--repo", required=True, help="owner/name")
    parser.add_argument("--branch", required=True, help="目标分支名")
    parser.add_argument("--junit", required=True, help="junit XML 路径")
    parser.add_argument("--run-id", default="", help="CI run id（写进报告里）")
    parser.add_argument("--sha", default="", help="触发本次 CI 的 commit")
    args = parser.parse_args(argv[1:])

    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN") or ""

    junit = Path(args.junit)
    failures = _read_failures(junit)

    header = f"# CI 失败报告\n\n- run id: {args.run_id or '(未知)'}\n"
    if args.sha:
        header += f"- commit: {args.sha}\n"
    header += "\n"

    files = {
        "failures.md": header + failures,
    }
    if junit.exists():
        files["pytest-report.xml"] = junit.read_text(encoding="utf-8", errors="replace")

    # ⚠️ **先走 git push 通道**（2026-11-26 实测）
    #   实测：REST API 那条路在这套环境里**一直没建出分支**
    #   （POST 422 / 权限 / 各种边角）⇒ 排查不了。
    #   而 ``git push`` 通道是**被验证过能通的**
    #   （开发者日常推送 ``develop`` 走的就是它）⇒ 放前面。
    #
    #   ⚠️ **git push 通道不需要 token** —— 认证由
    #   ``actions/checkout`` 配置的 extraheader 提供
    #   ⇒ 所以这一步在「没有 token」时也要能跑。
    if _git_push(args, files):
        print(f"✅ 失败报告已发布到分支 {args.branch}（git push 通道）")
        return 0

    print("   ⇒ git push 通道失败，退回 REST API 通道", file=sys.stderr)

    # ⚠️ **REST API 通道需要 token**（git push 通道不需要）
    if not token:
        print(
            "❌ git push 通道失败，且没有 GH_TOKEN / GITHUB_TOKEN "
            "⇒ 无法走 REST API 通道",
            file=sys.stderr,
        )
        return 1

    # ⚠️ **孤儿提交**（parent = null）——
    #   不依赖目标分支是否存在 ⇒ 第一次失败就能建出分支。
    tree = {
        "base_tree": None,
        "tree": [
            {
                "path": name,
                "mode": "100644",
                "type": "blob",
                "content": content,
            }
            for name, content in files.items()
        ],
    }

    status, resp = _post(f"{_API}/repos/{args.repo}/git/trees", token, tree)
    if status not in (200, 201):
        print(
            f"❌ 创建 tree 失败：HTTP {status} {resp}\n"
            f"   （token 长度 {len(token)}，repo={args.repo!r}）",
            file=sys.stderr,
        )
        return 1

    commit_payload = {
        "message": f"ci: 失败报告 run={args.run_id or 'unknown'}",
        "tree": resp["sha"],
        "parents": [],
    }
    status, commit = _post(
        f"{_API}/repos/{args.repo}/git/commits", token, commit_payload
    )
    if status not in (200, 201):
        print(f"❌ 创建 commit 失败：HTTP {status} {commit}", file=sys.stderr)
        return 1

    ref_payload = {
        "sha": commit["sha"],
        "force": True,
    }
    # ⚠️ **分支名里的 ``/`` 必须做 URL 编码**（实测踩坑）
    #   ``POST /repos/{owner}/{repo}/git/refs/heads/{ref}`` —— 这里的 ``{ref}``
    #   是**一个路径段**，而 GitHub 的路由把 ``/`` 当成**层级分隔符**。
    #   ⇒ 分支名 ``ci-diagnostics/failure-report`` 会被解析成
    #     ``refs/heads/ci-diagnostics`` + ``failure-report``（两段）
    #     ⇒ **422 Unprocessable Entity**，分支建不出来。
    #   ⇒ 用 ``quote`` 把整段编码成 ``ci-diagnostics%2Ffailure-report``。
    encoded_ref = quote(args.branch, safe="")
    status, ref = _post(
        f"{_API}/repos/{args.repo}/git/refs/heads/{encoded_ref}", token, ref_payload
    )
    # ⚠️ **分支已存在时 POST 会 422** ⇒ 退回 PATCH（更新现有 ref）。
    #   POST  = 创建；PATCH = 更新。
    if status == 422:
        status, ref = _patch(
            f"{_API}/repos/{args.repo}/git/refs/heads/{encoded_ref}",
            token,
            {"sha": commit["sha"], "force": True},
        )
    if status not in (200, 201):
        # ⚠️ **常见原因**：
        #   - 分支名里的 ``/`` 没编码（本次踩的坑）；
        #   - token 权限不足（job 级 permissions 没提到 ``contents: write``）。
        print(
            f"❌ 更新分支失败：HTTP {status} {ref}\n"
            f"   （分支名 {args.branch!r} → 编码后 {encoded_ref!r}）",
            file=sys.stderr,
        )
        return 1

    print(f"✅ 失败报告已发布到分支 {args.branch}（commit {commit['sha'][:7]}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
