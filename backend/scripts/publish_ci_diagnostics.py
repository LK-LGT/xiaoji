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

_API = os.environ.get("GITHUB_API_BASE") or "https://api.github.com"


def _post(url: str, token: str, payload: dict) -> tuple[int, dict]:
    """调 GitHub API（POST）。返回 ``(状态码, 响应 JSON)``。"""
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        method="POST",
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


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="发布 CI 失败报告到诊断分支")
    parser.add_argument("--repo", required=True, help="owner/name")
    parser.add_argument("--branch", required=True, help="目标分支名")
    parser.add_argument("--junit", required=True, help="junit XML 路径")
    parser.add_argument("--run-id", default="", help="CI run id（写进报告里）")
    parser.add_argument("--sha", default="", help="触发本次 CI 的 commit")
    args = parser.parse_args(argv[1:])

    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN") or ""
    if not token:
        print("⚠️ 没有 GH_TOKEN / GITHUB_TOKEN ⇒ 跳过发布", file=sys.stderr)
        return 0

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
        print(f"⚠️ 创建 tree 失败：HTTP {status} {resp}", file=sys.stderr)
        return 0

    commit_payload = {
        "message": f"ci: 失败报告 run={args.run_id or 'unknown'}",
        "tree": resp["sha"],
        "parents": [],
    }
    status, commit = _post(
        f"{_API}/repos/{args.repo}/git/commits", token, commit_payload
    )
    if status not in (200, 201):
        print(f"⚠️ 创建 commit 失败：HTTP {status} {commit}", file=sys.stderr)
        return 0

    ref_payload = {
        "sha": commit["sha"],
        "force": True,
    }
    status, ref = _post(
        f"{_API}/repos/{args.repo}/git/refs/heads/{args.branch}", token, ref_payload
    )
    if status not in (200, 201):
        print(f"⚠️ 更新分支失败：HTTP {status} {ref}", file=sys.stderr)
        return 0

    print(f"✅ 失败报告已发布到分支 {args.branch}（commit {commit['sha'][:7]}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
