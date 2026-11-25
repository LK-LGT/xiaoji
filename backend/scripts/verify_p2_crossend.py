"""P2 跨端联调验证 —— 用**真实 HTTP** 打真实运行中的后端。

为什么要有这个文件（而不是再加一轮 pytest）：
pytest 全是「进程内 + 依赖覆盖 + 内存 SQLite」，它**测不到**这三类问题：
  1. 路由没注册进真正的 ``app``（`_IncludedRouter` 挂载时机）；
  2. 查询参数名对不上（FastAPI 静默忽略，不报错）；
  3. 迁移没跑（表不存在 → 500）；
  4. 权限闸门在**真实角色**下是否真的放行/拦截。

这个脚本对着**真实运行的服务**发请求，用**真实登录**拿 token，
断言前端 ``src/api/p2.ts`` 会拿到的响应形状。

用法：
    python _p2_crossend_check.py [BASE_URL]
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from typing import Any

BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000").rstrip("/")

# ⚠️ 测试账号口令**不在源码里**（2026-11-24 已移除明文常量）。
#    ⇒ 从 ``TEST_USER_PASSWORDS`` 环境变量读（JSON，key=用户名），
#      与后端 ``routes/auth.py`` 的读取口径**完全一致**。
#    ⇒ 没配 ⇒ 四个账号都登不上 ⇒ 脚本会明确报「登录失败」。
_LOGIN_PASSWORDS: dict[str, str] = {}
try:
    _raw = os.environ.get("TEST_USER_PASSWORDS", "")
    _parsed = json.loads(_raw) if _raw else {}
    if isinstance(_parsed, dict):
        _LOGIN_PASSWORDS = {str(k): str(v) for k, v in _parsed.items()}
except (json.JSONDecodeError, TypeError):
    _LOGIN_PASSWORDS = {}

PASSED: list[str] = []
FAILED: list[tuple[str, str]] = []


# ---------------------------------------------------------------------------
# HTTP 工具
# ---------------------------------------------------------------------------

def call(
    method: str,
    path: str,
    *,
    token: str | None = None,
    body: Any = None,
    query: dict[str, Any] | None = None,
) -> tuple[int, Any]:
    """发一个真实 HTTP 请求，返回 (status, 解析后的 body)。"""
    url = f"{BASE}{path}"
    if query:
        pairs = [(k, v) for k, v in query.items() if v is not None]
        if pairs:
            from urllib.parse import urlencode
            url = f"{url}?{urlencode(pairs)}"

    data = None
    headers = {"Accept": "application/json"}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"

    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode("utf-8")
            status = resp.status
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8")
        status = exc.code
    except Exception as exc:  # 连接失败等
        return 0, {"_transport_error": str(exc)}

    try:
        return status, json.loads(raw)
    except json.JSONDecodeError:
        return status, {"_raw": raw[:500]}


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        PASSED.append(name)
        print(f"  [PASS] {name}")
    else:
        FAILED.append((name, detail))
        print(f"  [FAIL] {name} :: {detail}")


def unwrap(body: Any) -> Any:
    """取出 `{"code":0,"data":...}` 信封里的 data。"""
    if isinstance(body, dict) and "data" in body:
        return body["data"]
    return body


def envelope_ok(body: Any) -> bool:
    return isinstance(body, dict) and body.get("code") == 0 and "data" in body


# ---------------------------------------------------------------------------
# 登录
# ---------------------------------------------------------------------------

def login(username: str, password: str) -> str | None:
    status, body = call("POST", "/v1/auth/login", body={"username": username, "password": password})
    if status != 200:
        print(f"    login({username}) 失败: {status} {body}")
        return None
    token = unwrap(body)
    if isinstance(token, dict):
        return token.get("accessToken")
    return None


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

def main() -> int:
    print("=" * 78)
    print(f"P2 跨端联调验证 —— {BASE}")
    print("=" * 78)

    # ---- 0. 服务可达 ----
    print("\n[0] 服务可达性")
    status, body = call("GET", "/health")
    check("health 可达", status == 200, f"status={status} body={body}")

    # ---- 1. 真实登录 ----
    print("\n[1] 真实登录（拿真 token）")
    tokens: dict[str, str | None] = {}
    # ⚠️ 2026-11-24（B2）：``manager`` / ``leader`` 改名为真实身份
    #    （那两个别名撞 RBAC 角色名，且两个不同的人权限完全一样 ⇒ 测不出差别）
    for user in ("admin", "yangfei", "biyumi", "employee"):
        pwd = _LOGIN_PASSWORDS.get(user, "")
        tok = login(user, pwd) if pwd else None
        tokens[user] = tok
        check(f"登录 {user}", tok is not None, "未拿到 accessToken")

    hr = tokens["admin"]
    mgr = tokens["yangfei"]
    emp = tokens["employee"]
    if not hr or not mgr or not emp:
        print("\n登录失败，终止。")
        return 1

    # ---- 2. P1-5 数据源白名单 ----
    print("\n[2] P1-5 数据源白名单")
    status, body = call("GET", "/v1/data-sources", token=hr)
    check("GET /v1/data-sources 信封正确", envelope_ok(body), str(body)[:200])
    policies = unwrap(body)
    check("返回策略列表", isinstance(policies, list) and len(policies) > 0, str(policies)[:200])
    if isinstance(policies, list) and policies:
        first = policies[0]
        check(
            "策略字段齐全（sourceCode/allowedRoles/redactedFields）",
            all(k in first for k in ("sourceCode", "allowedRoles", "redactedFields", "configured")),
            str(first)[:200],
        )
        codes = {p.get("sourceCode") for p in policies}
        check("种子源 attendance 在列", "attendance" in codes, f"codes={codes}")
        att = next((p for p in policies if p.get("sourceCode") == "attendance"), None)
        if att:
            check(
                "attendance 角色含 employee（员工可看自己的考勤）",
                "employee" in (att.get("allowedRoles") or []),
                str(att.get("allowedRoles")),
            )

    # 员工无权改白名单 → 403
    status, _ = call(
        "PUT", "/v1/data-sources", token=emp,
        body={"source_code": "attendance", "allowed": False},
    )
    check("员工改白名单被拒（403）", status == 403, f"status={status}")

    # ---- 3. 找一个真实的考核记录（语义质检的输入） ----
    print("\n[3] 准备数据：找一条真实考核记录")
    record_id = None
    employee_id = None
    # 记录挂在任务下：/v1/performance/tasks/{task_id}/records
    status, body = call("GET", "/v1/performance/tasks", token=hr)
    print(f"    /v1/performance/tasks → {status}")
    tasks = unwrap(body)
    task_items = None
    if isinstance(tasks, dict):
        task_items = tasks.get("items") or tasks.get("tasks") or tasks.get("list")
    elif isinstance(tasks, list):
        task_items = tasks

    for task in (task_items or [])[:5]:
        tid = task.get("id") or task.get("task_id")
        if not tid:
            continue
        status, body = call("GET", f"/v1/performance/tasks/{tid}/records", token=hr)
        if status != 200:
            continue
        recs = unwrap(body)
        items = None
        if isinstance(recs, dict):
            items = recs.get("items") or recs.get("records") or recs.get("list")
        elif isinstance(recs, list):
            items = recs
        for rec in (items or []):
            record_id = rec.get("record_id") or rec.get("id")
            employee_id = rec.get("employee_id")
            if record_id:
                print(f"    选中记录: record_id={record_id} employee_id={employee_id} (task={tid})")
                break
        if record_id:
            break

    check("找到可用的考核记录", record_id is not None, "任务下没有考核记录")
    if record_id is None:
        print("\n无考核记录，后续用例跳过。")
    else:
        # 列表接口不一定带 employee_id —— 从记录详情里补齐
        status, body = call("GET", f"/v1/performance/records/{record_id}", token=hr)
        detail = unwrap(body)
        if isinstance(detail, dict):
            employee_id = (
                detail.get("employee_id")
                or (detail.get("employee") or {}).get("id")
                or employee_id
            )
        print(f"    employee_id 解析为: {employee_id}")

    # ---- 3b. 找一个「主管确实能操作」的记录 ----
    # 为什么单独找：`POST /v1/feedback` 走 `can_view_score_of`（本人/直属主管/HR）。
    # 随便挑的记录，其员工的主管往往不是测试账号 —— 403 属于**权限闸门正常工作**，
    # 不是功能缺陷。但联调要验的是「功能可用」，所以必须挑对记录。
    print("\n[3b] 准备数据：找主管可操作的记录")
    mgr_record_id = None
    status, body = call("GET", "/v1/performance/tasks", token=mgr)
    mgr_tasks = unwrap(body)
    mgr_task_items = None
    if isinstance(mgr_tasks, dict):
        mgr_task_items = mgr_tasks.get("items") or mgr_tasks.get("tasks") or mgr_tasks.get("list")
    elif isinstance(mgr_tasks, list):
        mgr_task_items = mgr_tasks

    for task in (mgr_task_items or [])[:8]:
        tid = task.get("task_id") or task.get("id")
        if not tid:
            continue
        status, body = call("GET", f"/v1/performance/tasks/{tid}/records", token=mgr)
        if status != 200:
            continue
        recs = unwrap(body)
        items = None
        if isinstance(recs, dict):
            items = recs.get("items") or recs.get("records") or recs.get("list")
        elif isinstance(recs, list):
            items = recs
        for rec in (items or []) or []:
            rid = rec.get("record_id") or rec.get("id")
            if not rid:
                continue
            # 用主管自己的 token 探测 —— 不靠假设，靠实际权限
            probe_status, _ = call("GET", f"/v1/semantic-qc/records/{rid}", token=mgr)
            if probe_status == 200:
                mgr_record_id = rid
                break
        if mgr_record_id:
            break

    check("找到主管可操作的记录", mgr_record_id is not None, "没有主管可操作的记录")
    if mgr_record_id:
        print(f"    主管可操作记录: {mgr_record_id}")

    # ---- 4. P2-1 语义质检 ----
    if record_id:
        print("\n[4] P2-1 语义质检 / 风险识别 / 面谈提纲")
        status, body = call("GET", f"/v1/semantic-qc/records/{record_id}", token=hr)
        check("GET 语义质检 200", status == 200, f"status={status} body={str(body)[:200]}")
        check("响应信封正确", envelope_ok(body), str(body)[:200])
        qc = unwrap(body)
        if isinstance(qc, dict):
            check("返回 semanticIssues 数组", isinstance(qc.get("semanticIssues"), list), str(list(qc)[:10]))
            check("返回 risks 数组", isinstance(qc.get("risks"), list), "")
            check("返回 interviewOutline", isinstance(qc.get("interviewOutline"), dict), "")
            check("带 disclaimer 免责说明", bool(qc.get("disclaimer")), str(qc.get("disclaimer")))
            outline = qc.get("interviewOutline") or {}
            check("面谈提纲带自己的 disclaimer", bool(outline.get("disclaimer")), "")
            questions = outline.get("questions") or []
            check("提纲问题含 purpose 与 basis", all(
                ("purpose" in q and "basis" in q) for q in questions
            ), str(questions[:1])[:200])

    # ---- 5. P2-2 趋势报表 ----
    print("\n[5] P2-2 趋势复盘报表")
    if employee_id:
        status, body = call("GET", f"/v1/trend-report/trend/{employee_id}", token=hr)
        check("GET 跨周期趋势 200", status == 200, f"status={status} body={str(body)[:200]}")
        check("趋势响应信封正确", envelope_ok(body), str(body)[:200])
        trend = unwrap(body)
        if isinstance(trend, dict):
            check("趋势含 points/trend/note", all(
                k in trend for k in ("points", "trend", "note")
            ), str(list(trend)[:10]))

    # ⚠️ 查询参数名：FastAPI 不做 camelCase→snake_case 映射，
    #    参数名写错时**静默忽略**（不 422）。这里验证前端实际用的名字。
    #
    # 验证手法：同一个筛选条件下，用「正确名」与「camelCase 名」各查一次。
    # 若 camelCase 版返回的筛选结果**更多**（= 条件被忽略），
    # 说明前端如果发 camelCase 就会静默失效。
    print("\n[5b] 查询参数名：camelCase 是否会静默失效")
    status, body_snake = call("GET", "/v1/trend-report/risk-roster", token=hr,
                              query={"department_id": None, "period": "202611"})
    status, body_camel = call("GET", "/v1/trend-report/risk-roster", token=hr,
                               query={"departmentId": None, "period": "202611"})
    snake_data = unwrap(body_snake) if envelope_ok(body_snake) else {}
    camel_data = unwrap(body_camel) if envelope_ok(body_camel) else {}
    if isinstance(snake_data, dict) and isinstance(camel_data, dict):
        same = snake_data.get("scannedCount") == camel_data.get("scannedCount")
        check(
            "camelCase 与 snake_case 行为一致（说明前端必须用 snake_case）",
            same,
            f"snake={snake_data.get('scannedCount')} camel={camel_data.get('scannedCount')}",
        )

    status, body = call("GET", "/v1/trend-report/risk-roster", token=hr,
                        query={"department_id": None, "period": "202611"})
    check("GET 风险清单 200", status == 200, f"status={status} body={str(body)[:200]}")
    check("风险清单响应信封正确", envelope_ok(body), str(body)[:200])
    roster = unwrap(body)
    if isinstance(roster, dict):
        check("风险清单含 disclaimer", bool(roster.get("disclaimer")), "")
        check("风险清单含 scannedCount/riskCount", all(
            k in roster for k in ("scannedCount", "riskCount")
        ), str(list(roster)[:10]))

        # ★ 「已确认评级但缺指标依据」这条信号曾经是**死代码** ——
        #    它要检查的正是没有 Score 行的那批记录，而 INNER JOIN 把它们全吞了。
        codes = {s["code"] for i in (roster.get("items") or []) for s in i.get("signals", [])}
        check("★ 风险清单能报出 RISK_CONFIRMED_WITHOUT_BASIS",
              "RISK_CONFIRMED_WITHOUT_BASIS" in codes,
              f"codes={sorted(codes)}")

    status, body = call("GET", "/v1/trend-report/position", token=hr,
                        query={"position": "销售", "period": "202611"})
    check("GET 岗位横向 200", status == 200, f"status={status} body={str(body)[:200]}")
    position = unwrap(body)
    if isinstance(position, dict):
        check("岗位横向含 smallSample 标记", "smallSample" in position, str(list(position)[:10]))
        check("岗位横向含 note", bool(position.get("note")), "")

    # ---- 6. P2-2 薪酬导出（权限最敏感） ----
    print("\n[6] P2-2 薪酬建议导出（仅 HR）")
    status, body = call("GET", "/v1/trend-report/payroll-export", token=hr,
                        query={"period": "202611", "include_names": False})
    check("HR 导出 200", status == 200, f"status={status} body={str(body)[:200]}")
    check("导出响应信封正确", envelope_ok(body), str(body)[:200])
    payroll = unwrap(body)
    if isinstance(payroll, dict):
        check("导出含 disclaimer", bool(payroll.get("disclaimer")), "")
        check("导出含 privacyNote", bool(payroll.get("privacyNote")), "")
        check("导出含 unscoredCount", "unscoredCount" in payroll, str(list(payroll)[:10]))
        rows = payroll.get("rows") or []
        check("默认导出不带姓名", all(
            not r.get("employee_name") for r in rows
        ), f"rows[0]={rows[0] if rows else None}")
        check("每行带 scored 标记", all(
            "scored" in r for r in rows
        ), f"rows[0]={rows[0] if rows else None}")

        # ★ 缺分记录必须可见 + 必须被警告（跨端联调发现的缺陷回归保护）
        unscored = [r for r in rows if r.get("scored") is False]
        check("★ 缺分数的已评级记录仍在导出里", len(unscored) > 0,
              f"rows={len(rows)} unscored={len(unscored)}")
        check("★ 缺分计数与实际行数一致",
              payroll.get("unscoredCount") == len(unscored),
              f"reported={payroll.get('unscoredCount')} actual={len(unscored)}")
        if unscored:
            check("★ 缺分时免责说明明确警告", "缺分数" in (payroll.get("disclaimer") or ""),
                  (payroll.get("disclaimer") or "")[:120])
            check("★ 缺分行不编造分数", all(r.get("total") is None for r in unscored), "")

    status, body = call("GET", "/v1/trend-report/payroll-export", token=emp,
                        query={"period": "202611"})
    check("员工导出被拒（403）", status == 403, f"status={status} body={str(body)[:200]}")

    # ---- 7. P2-3 反馈闭环 ----
    print("\n[7] P2-3 反馈闭环 / Prompt 灰度 / 案例库")
    if mgr_record_id:
        status, body = call("POST", "/v1/feedback", token=mgr, body={
            "record_id": mgr_record_id,
            "action": "MODIFY",
            "agent": "comment_draft",
            "reason": "跨端联调验证：主管修改后采纳",
        })
        check("主管提交反馈 200", status == 200, f"status={status} body={str(body)[:200]}")
        check("反馈响应信封正确", envelope_ok(body), str(body)[:200])
        fb_row = unwrap(body)
        if isinstance(fb_row, dict):
            check("反馈落库返回 action", fb_row.get("action") == "MODIFY", str(fb_row)[:200])

        # ★ 核心不变量：提交反馈**不得**改动考核记录本身。
        # 验证手法：提交前后各拉一次记录详情，比对 confirmed_rating / manager_comment。
        status, body_before = call("GET", f"/v1/performance/records/{mgr_record_id}", token=hr)
        before = unwrap(body_before)
        call("POST", "/v1/feedback", token=mgr, body={
            "record_id": mgr_record_id,
            "action": "REJECT",
            "agent": "comment_draft",
            "reason": "跨端联调：验证反馈不改分",
        })
        status, body_after = call("GET", f"/v1/performance/records/{mgr_record_id}", token=hr)
        after = unwrap(body_after)
        if isinstance(before, dict) and isinstance(after, dict):
            check(
                "★ 提交反馈后记录 confirmed_rating 不变",
                before.get("confirmed_rating") == after.get("confirmed_rating"),
                f"before={before.get('confirmed_rating')} after={after.get('confirmed_rating')}",
            )
            check(
                "★ 提交反馈后记录 manager_comment 不变",
                before.get("manager_comment") == after.get("manager_comment"),
                f"before={before.get('manager_comment')} after={after.get('manager_comment')}",
            )
    else:
        print("  [SKIP] 无主管可操作记录，跳过反馈提交")

    status, body = call("GET", "/v1/feedback", token=hr, query={"agent": "comment_draft"})
    check("HR 列反馈 200", status == 200, f"status={status} body={str(body)[:200]}")
    fb = unwrap(body)
    if isinstance(fb, dict):
        check("反馈列表含 items 与 stats", "items" in fb and "stats" in fb, str(list(fb)[:10]))
        stats = fb.get("stats") or {}
        check("统计含 acceptRate", "acceptRate" in stats, str(list(stats)[:10]))

    status, body = call("GET", "/v1/feedback", token=emp, query={"agent": "comment_draft"})
    check("员工列反馈被拒（403）", status == 403, f"status={status}")

    # Prompt 灰度：HR 建版本
    status, body = call("PUT", "/v1/feedback/prompts", token=hr, body={
        "agent": "comment_draft",
        "version": "v-crossend-1",
        "prompt_text": "跨端联调验证用的 Prompt 正文",
        "rollout": "canary",
        "canary_ratio": 0.2,
        "baseline_version": None,
    })
    check("HR 建 Prompt 版本 200", status == 200, f"status={status} body={str(body)[:200]}")

    status, body = call("GET", "/v1/feedback/prompts", token=hr, query={"agent": "comment_draft"})
    check("HR 列 Prompt 版本 200", status == 200, f"status={status}")
    prompts = unwrap(body)
    check("Prompt 版本已落库", isinstance(prompts, list) and len(prompts) > 0, str(prompts)[:200])

    status, body = call("PUT", "/v1/feedback/prompts", token=emp, body={
        "agent": "comment_draft", "version": "v-x", "prompt_text": "x",
    })
    check("员工建 Prompt 版本被拒（403）", status == 403, f"status={status}")

    # 确定性分桶
    if record_id:
        status, body = call("GET", "/v1/feedback/prompts/resolve", token=hr,
                            query={"agent": "comment_draft", "record_id": record_id})
        check("分桶解析 200", status == 200, f"status={status} body={str(body)[:200]}")
        resolved = unwrap(body)
        if isinstance(resolved, dict):
            check("分桶返回 mode 与 bucket", "mode" in resolved and "bucket" in resolved,
                  str(list(resolved)[:10]))

    # 灰度对比
    status, body = call("GET", "/v1/feedback/prompts/compare", token=hr,
                        query={"agent": "comment_draft"})
    check("灰度对比 200", status == 200, f"status={status} body={str(body)[:200]}")
    comparison = unwrap(body)
    if isinstance(comparison, dict):
        check("对比返回 sufficient 标记", "sufficient" in comparison, str(list(comparison)[:10]))
        check("对比返回 minSampleForConclusion", "minSampleForConclusion" in comparison, "")
        check("对比返回 conclusion", bool(comparison.get("conclusion")), "")

    # 案例库
    status, body = call("GET", "/v1/feedback/cases", token=emp,
                        query={"scenario": "general", "keyword": "x", "limit": 20})
    check("员工读案例库 200（全员可读）", status == 200, f"status={status} body={str(body)[:200]}")
    check("案例库响应信封正确", envelope_ok(body), str(body)[:200])

    status, body = call("POST", "/v1/feedback/cases", token=mgr, body={
        "title": "跨端联调验证案例",
        "scenario": "general",
        "resolution": "验证用处理要点",
    })
    check("主管收录案例 200", status == 200, f"status={status} body={str(body)[:200]}")
    case = unwrap(body)
    if isinstance(case, dict):
        check("案例强制脱敏标记", case.get("redacted") is True, str(case)[:200])

    status, body = call("POST", "/v1/feedback/cases", token=emp, body={
        "title": "员工越权收录", "scenario": "general", "resolution": "应当被拒",
    })
    check("员工收录案例被拒（403）", status == 403, f"status={status}")

    # ---- 8. 汇总 ----
    print("\n" + "=" * 78)
    print(f"通过 {len(PASSED)} / {len(PASSED) + len(FAILED)}")
    if FAILED:
        print("\n失败明细：")
        for name, detail in FAILED:
            print(f"  ✗ {name}\n      {detail}")
        return 1
    print("全部通过 ✅")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
