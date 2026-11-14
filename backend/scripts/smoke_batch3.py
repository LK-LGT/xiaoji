#!/usr/bin/env python
"""批次 3 端点冒烟测试。

覆盖批次 3 新增的全部端点（employee/ai、employee/week-plans、system/ai、
dispute/cases，以及 leader 建议/项目包、system/users、employee/results、
employee/week-results、employee/plan-draft-versions、ai/reviews、
department 结果确认与周成果审批）。

逐个发真实 HTTP 请求，校验：
  1. 路径可路由（非 404）
  2. 请求体可解析（非 422 —— 裸 dict 注解会导致 body 以字符串传入）
  3. 响应结构符合前端类型约定

用法：
    python backend/scripts/smoke_batch3.py
    python backend/scripts/smoke_batch3.py --base http://localhost:8000
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request

# (method, path, body, expect)
# expect: 200 = 正常 2xx；401 = 需要认证（说明路由与 body 解析都正常）
#        404 = 下载类端点故意返回 404（无资源时避免前端拿到损坏文件）
TESTS: list[tuple[str, str, dict | None, int]] = [
    # ── dispute/cases（10）──────────────────────────────────────────────
    ("GET", "/v1/dispute/cases", None, 200),
    ("GET", "/v1/dispute/cases/1", None, 200),
    ("GET", "/v1/dispute/cases/1/reviewers", None, 200),
    ("GET", "/v1/dispute/cases/1/reviewer-candidates", None, 200),
    ("POST", "/v1/dispute/cases/1/reviewers", {"userId": 5}, 200),
    ("DELETE", "/v1/dispute/cases/1/reviewers/2", None, 200),
    ("POST", "/v1/dispute/cases/1/recusal", {"reason": "r"}, 200),
    ("POST", "/v1/dispute/cases/1/opinions", {"opinion": "SUPPORT", "comment": "c"}, 200),
    ("POST", "/v1/dispute/cases/1/decision", {"decision": "SUPPORT", "comment": "c"}, 200),
    ("GET", "/v1/dispute/cases/1/package", None, 404),
    # ── system/ai（10）──────────────────────────────────────────────────
    ("GET", "/v1/system/ai/configs", None, 200),
    ("POST", "/v1/system/ai/configs", {"configName": "x", "modelName": "m"}, 200),
    ("PUT", "/v1/system/ai/configs/1", {"configName": "x"}, 200),
    ("POST", "/v1/system/ai/configs/1/enable", None, 200),
    ("POST", "/v1/system/ai/configs/1/test", None, 200),
    ("GET", "/v1/system/ai/prompts", None, 200),
    ("POST", "/v1/system/ai/prompts/publish", {"sceneCode": "S", "versionNo": "1"}, 200),
    ("GET", "/v1/system/ai/metrics", None, 200),
    ("GET", "/v1/system/ai/call-logs", None, 200),
    ("GET", "/v1/system/ai/call-logs/1", None, 200),
    # ── employee/ai（16）────────────────────────────────────────────────
    ("GET", "/v1/employee/ai/month-plans/context?planMonth=2026-11", None, 200),
    ("GET", "/v1/employee/ai/week-plans/context?weekStart=2026-11-02", None, 200),
    ("GET", "/v1/employee/ai/day-plans/context?planDate=2026-11-02", None, 200),
    ("POST", "/v1/employee/ai/month-plans/generate", {"requestId": "r1"}, 200),
    ("POST", "/v1/employee/ai/month-plans/check", {"requestId": "r1"}, 200),
    ("POST", "/v1/employee/ai/month-plans/items/optimize", {"requestId": "r1"}, 200),
    ("POST", "/v1/employee/ai/week-plans/generate", {"requestId": "r1"}, 200),
    ("POST", "/v1/employee/ai/week-plans/adjust", {"requestId": "r1"}, 200),
    ("POST", "/v1/employee/ai/day-plans/generate", {"requestId": "r1"}, 200),
    ("POST", "/v1/employee/ai/day-plans/adjust", {"requestId": "r1"}, 200),
    ("POST", "/v1/employee/ai/suggestions/s1/actions", {"actionCode": "PREVIEW"}, 200),
    ("GET", "/v1/employee/ai/conversations/active", None, 200),
    ("GET", "/v1/employee/ai/conversations/history", None, 200),
    ("GET", "/v1/employee/ai/conversations/1/messages", None, 200),
    ("POST", "/v1/employee/ai/conversations/new", {"sceneCode": "S"}, 200),
    # ── employee/week-plans（10）────────────────────────────────────────
    ("GET", "/v1/employee/week-plans", None, 401),
    ("GET", "/v1/employee/week-plans/parent-options", None, 401),
    ("GET", "/v1/employee/week-plans/1", None, 401),
    ("POST", "/v1/employee/week-plans", {"weekStart": "2026-11-02"}, 401),
    ("PUT", "/v1/employee/week-plans/1", {"weekStart": "2026-11-02"}, 401),
    ("DELETE", "/v1/employee/week-plans/1", None, 401),
    ("POST", "/v1/employee/week-plans/1/submit", None, 401),
    ("POST", "/v1/employee/week-plans/1/withdraw", None, 401),
    ("GET", "/v1/employee/week-plans/1/items/2/results/submit-context", None, 401),
    ("POST", "/v1/employee/week-plans/1/items/2/results/submit", None, 401),
    # ── leader/result-suggestions（7）───────────────────────────────────
    ("GET", "/v1/leader/result-suggestions", None, 200),
    ("GET", "/v1/leader/result-suggestions/s1", None, 200),
    ("POST", "/v1/leader/result-suggestions/s1/suggest", {"action": "SUGGEST"}, 200),
    ("POST", "/v1/leader/result-suggestions/batch-suggest", {"ids": ["a", "b"]}, 200),
    ("GET", "/v1/leader/result-suggestions/s1/evidence/1", None, 404),
    ("GET", "/v1/leader/result-suggestions/s1/evidence/1/preview", None, 200),
    ("GET", "/v1/leader/result-suggestions/s1/evidence/1/archive-entry?path=a.txt", None, 404),
    # ── leader 项目包（8）──────────────────────────────────────────────
    ("GET", "/v1/leader/project-day-plan-packages", None, 200),
    ("GET", "/v1/leader/project-day-plan-packages/1", None, 200),
    ("GET", "/v1/leader/project-week-plan-packages", None, 200),
    ("GET", "/v1/leader/project-week-plan-packages/1", None, 200),
    ("GET", "/v1/leader/project-week-plan-packages/1/excel", None, 404),
    ("GET", "/v1/leader/project-week-result-packages", None, 200),
    ("GET", "/v1/leader/project-week-result-packages/1", None, 200),
    ("GET", "/v1/leader/project-week-result-packages/1/excel", None, 404),
    # ── system/users（7）────────────────────────────────────────────────
    ("GET", "/v1/system/users", None, 200),
    ("POST", "/v1/system/users/register", {"realName": "张三", "mobile": "13800000000", "deptId": 1, "roleId": 1}, 200),
    ("PUT", "/v1/system/users/1", {"realName": "张三"}, 200),
    ("POST", "/v1/system/users/1/status", {"status": 1}, 200),
    ("POST", "/v1/system/users/1/reset-password", None, 200),
    ("GET", "/v1/system/users/import-template", None, 404),
    ("GET", "/v1/system/users/export", None, 404),
    # ── employee/results（5）────────────────────────────────────────────
    ("GET", "/v1/employee/results/submit/options", None, 200),
    ("GET", "/v1/employee/results/1", None, 200),
    ("GET", "/v1/employee/results/1/evidence/1", None, 404),
    ("POST", "/v1/employee/results/submit/excel", {"monthPlanId": 1, "rows": []}, 404),
    # ── employee/week-results（3）──────────────────────────────────────
    ("GET", "/v1/employee/week-results/submit/options", None, 200),
    ("GET", "/v1/employee/week-results/1", None, 200),
    ("GET", "/v1/employee/week-results/1/evidence/1", None, 404),
    # ── employee/plan-draft-versions（3）────────────────────────────────
    ("GET", "/v1/employee/plan-draft-versions?planType=MONTH&periodKey=2026-11", None, 200),
    ("GET", "/v1/employee/plan-draft-versions/1", None, 200),
    ("GET", "/v1/employee/plan-draft-versions/1/restore?planType=MONTH&periodKey=2026-11", None, 200),
    # ── ai/reviews（5）──────────────────────────────────────────────────
    ("GET", "/v1/ai/reviews/capability", None, 200),
    ("GET", "/v1/ai/reviews/latest?bizType=MONTH_PLAN&bizId=1", None, 200),
    ("POST", "/v1/ai/reviews/plans/MONTH_PLAN/1/check", None, 200),
    ("POST", "/v1/ai/reviews/plans/MONTH_PLAN/1/ensure", None, 200),
    # ── department/result-confirms（4）──────────────────────────────────
    ("GET", "/v1/department/result-confirms/r1", None, 200),
    ("GET", "/v1/department/result-confirms/r1/evidence/1", None, 404),
    ("POST", "/v1/department/result-confirms/r1/confirm", {"action": "CONFIRM"}, 200),
    ("POST", "/v1/department/result-confirms/r1/reject", {"action": "REJECT"}, 200),
    # ── department/week-plans（1）───────────────────────────────────────
    ("GET", "/v1/department/week-plans/1", None, 200),
    # ── week-result-approvals（5）───────────────────────────────────────
    ("POST", "/v1/leader/week-result-approvals/1/approve", {"comment": "ok"}, 200),
    ("POST", "/v1/leader/week-result-approvals/1/reject", {"comment": "no"}, 200),
    ("POST", "/v1/department/week-result-approvals/1/approve", {"comment": "ok"}, 200),
    ("POST", "/v1/department/week-result-approvals/1/reject", {"comment": "no"}, 200),
    ("GET", "/v1/leader/week-result-approvals/1/evidence/1", None, 404),
    # ── leader/ai/month-plan-context（2）────────────────────────────────
    ("GET", "/v1/leader/ai/month-plan-context?planMonth=2026-11&orgId=1", None, 200),
    ("PUT", "/v1/leader/ai/month-plan-context", {"planMonth": "2026-11", "orgId": 1}, 200),
    # ── ai/requests（1）─────────────────────────────────────────────────
    ("POST", "/v1/ai/requests/r1/cancel", None, 200),
]

# multipart 上传端点单独校验（不能用 JSON body 测）
MULTIPART_TESTS: list[tuple[str, str, int]] = [
    ("/v1/employee/ai/plan-examples/extract", "plan.txt", 200),
    ("/v1/system/users/import", "users.xlsx", 200),
]


def call(base: str, method: str, path: str, body: dict | None) -> tuple[int, bytes]:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    headers = {"Content-Type": "application/json"} if data else {}
    req = urllib.request.Request(base + path, method=method, data=data, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def call_multipart(base: str, path: str, filename: str, content: bytes) -> tuple[int, bytes]:
    """发一个 multipart/form-data 请求（模拟前端 FormData 上传）。"""
    boundary = "----batch3boundary"
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
        "Content-Type: application/octet-stream\r\n\r\n"
    ).encode("utf-8") + content + f"\r\n--{boundary}--\r\n".encode("utf-8")
    req = urllib.request.Request(
        base + path,
        method="POST",
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def main() -> int:
    parser = argparse.ArgumentParser(description="批次 3 端点冒烟测试")
    parser.add_argument("--base", default="http://localhost:8000")
    args = parser.parse_args()

    passed = 0
    failures: list[tuple[str, str, int, str]] = []

    for method, path, body, expect in TESTS:
        try:
            status, _ = call(args.base, method, path, body)
        except Exception as exc:  # noqa: BLE001
            failures.append((method, path, -1, str(exc)))
            print("x", end="", flush=True)
            continue
        if status == expect:
            passed += 1
            print(".", end="", flush=True)
        else:
            failures.append((method, path, status, f"期望 {expect}，实际 {status}"))
            print("X", end="", flush=True)

    # multipart 上传端点单独校验（不能用 JSON body 测）
    for upload_path, filename, expect in MULTIPART_TESTS:
        try:
            status, _ = call_multipart(args.base, upload_path, filename, b"sample content")
        except Exception as exc:  # noqa: BLE001
            status, detail = -1, str(exc)
        else:
            detail = "" if status == expect else f"期望 {expect}，实际 {status}"
        if status == expect:
            passed += 1
            print(".", end="", flush=True)
        else:
            failures.append(("POST", upload_path, status, detail or "未知错误"))
            print("X", end="", flush=True)

    total = len(TESTS) + len(MULTIPART_TESTS)
    print()
    print("=" * 70)
    print(f"批次 3 端点冒烟测试: {passed}/{total} 通过")
    if failures:
        print("-" * 70)
        for method, path, status, detail in failures:
            print(f"  FAIL {method:<7}{path:<60} {detail}")
        return 1
    print("✅ 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
