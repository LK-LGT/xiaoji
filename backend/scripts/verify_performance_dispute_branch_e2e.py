"""★ 绩效**异议分支** E2E 验证（CI-5 阶段 3 第二步）—— 真实库跑完整条链路。

⚠️ **为什么要有这个脚本**
------------------------------------------------------------------
``verify_performance_closed_loop_e2e.py`` 验的是**顺利路径**
（填报 → 评分 → 面谈 → 确认 → 定稿 → 归档）。

⚠️ **但异议分支才是最容易出事的地方**
------------------------------------------------------------------
员工**提出异议**后，链路会**绕回去**：

    ⑦ 员工提出异议 (dispute)   → phase = disputed
    ⑧ HR 退回重新评分 (reopen)  → phase = reviewing
                                    ⚠️ 记录状态**退回** self_review_submitted
                                    ⚠️ **score 行被删除**
                                    ⚠️ 旧分数**存进 previous_results** 历史
    ⑨ 主管重新评分              → 重新算分、重新落库
    ⑩ 面谈 → 确认 → 定稿       → 又走一遍

⇒ 这条路径上**有 4 个「删除/回退」动作**，每一个都可能出错：
- ``reopen`` 要**删掉** ``score`` 行 —— 而 ``review_score_version``
  是 **append-only** 的 ⇒ 删 ``score`` 会不会连带触发历史表的保护？
- 退回后**主管能不能真的重新评分**？（状态是 ``self_review_submitted``，
  是不是会被「自评已提交，不能覆盖」挡住？）
- 重新评分后**分数会不会翻倍**（旧 score 没删干净 ⇒ 出现两行）？
- ``confirmed_rating`` 有没有**被正确清空**（退回后不该还留着等级）？

⚠️ **这些在正向脚本里全都验不到** ⇒ 本脚本专门补这一段。

⚠️ **本脚本只跑一条链路**（定量方案），且用**全新 ID**
------------------------------------------------------------------
理由同 ``verify_performance_closed_loop_e2e.py``：
``review_score_version`` 是 append-only 的 ⇒ 固定 ID + 先删后建的重跑方式必然失败。

⚠️ **三条铁律**
------------------------------------------------------------------
1. ``get_db`` 覆盖必须用 **generator** 形式且**必须 commit**。
2. ``get_current_user`` 覆盖不能塞**已关闭会话**的 Employee 实例。
3. ``revision`` 必须**从库里读**（接口返回体不含它）。

用法::

    python backend/scripts/verify_performance_dispute_branch_e2e.py
"""

from __future__ import annotations

import sys
import uuid
from pathlib import Path

# 让脚本能从仓库根目录直接运行
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import select  # noqa: E402

from backend.app.core.db import get_db, get_sessionmaker  # noqa: E402
from backend.app.dependencies import get_current_user  # noqa: E402
from backend.app.domain.models import (  # noqa: E402
    AssessmentRecord,
    AssessmentTask,
    ConfigSnapshot,
    Department,
    Employee,
    HrDepartmentConfig,
    Score,
)
from backend.app.main import app  # noqa: E402
from backend.app.services import scheme_service as service  # noqa: E402

# ═══════════════════════════════════════════════════════════════════════════
# 隔离标记
# ═══════════════════════════════════════════════════════════════════════════

TAG = "E2E_DISP"

PERIOD = "209901"
PERIOD_TYPE = "monthly"
SELF_DEADLINE = "2099-02-01T12:00:00"
MANAGER_DEADLINE = "2099-02-05T12:00:00"

#: 每轮全新 ID（append-only ⇒ 不能复用，见模块 docstring）
RUN_SUFFIX = uuid.uuid4().hex[:8]

HR_ID = f"{TAG}_{RUN_SUFFIX}_HR"
LEADER_ID = f"{TAG}_{RUN_SUFFIX}_L"
EMP_ID = f"{TAG}_{RUN_SUFFIX}_E"
HR_DEPT_ID = f"{TAG}_{RUN_SUFFIX}_DEPT_H"
BIZ_DEPT_ID = f"{TAG}_{RUN_SUFFIX}_DEPT_B"

FAILED = False
PASSED: list[str] = []


def log(stage: str, message: str, ok: bool = True) -> None:
    global FAILED
    mark = "✔" if ok else "✖"
    print(f"{mark} [{stage}] {message}")
    if ok:
        PASSED.append(f"[{stage}] {message}")
    else:
        FAILED = True


def section(title: str) -> None:
    print(f"\n── {title} " + "─" * max(0, 60 - len(title)))


def _current_revision(db, record_id: str) -> int:
    """读当前填报的 ``revision``。

    ⚠️ **接口返回体不含 revision**（只有 ``{"record_id", "status"}``）
    ⇒ 必须从库里读，否则下一次请求必 409。

    ⚠️ **用 ORM 而不是裸 SQL**
    ``values`` 是 SQLite 保留字，裸 SQL 里要加引号（``"values"``），
    用 ORM 则由 SQLAlchemy 自动转义。
    """
    from backend.app.domain.scheme_models import SchemeResponse

    row = db.get(SchemeResponse, record_id)
    return int(row.revision) if row else 0


# ═══════════════════════════════════════════════════════════════════════════
# 准备
# ═══════════════════════════════════════════════════════════════════════════


def build_people(db) -> dict[str, Employee]:
    """建 HR / 主管 / 员工 三个人。

    ⚠️ **汇报线必须真实存在** —— 主管能看到员工的数据范围靠 ``manager_id``。
    """
    db.add_all(
        [
            Department(id=HR_DEPT_ID, name=f"{TAG}人事"),
            Department(id=BIZ_DEPT_ID, name=f"{TAG}业务", leader_id=LEADER_ID),
        ]
    )
    db.flush()
    people = {
        "hr": Employee(
            id=HR_ID, name=f"{TAG}HR", department_id=HR_DEPT_ID, position="HR"
        ),
        "leader": Employee(
            id=LEADER_ID,
            name=f"{TAG}主管",
            department_id=BIZ_DEPT_ID,
            position="负责人",
        ),
        "employee": Employee(
            id=EMP_ID,
            name=f"{TAG}员工",
            department_id=BIZ_DEPT_ID,
            position="销售",
            manager_id=LEADER_ID,
        ),
    }
    db.add_all(people.values())
    db.add(HrDepartmentConfig(department_id=HR_DEPT_ID, active=True))
    db.commit()
    return people


def install_overrides(people: dict[str, Employee], db) -> None:
    """装上 ``get_db`` / ``get_current_user`` 覆盖。"""

    def session():
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise

    app.dependency_overrides[get_db] = session
    app.dependency_overrides[get_current_user] = lambda: people["current"]


def scheme_payload() -> dict:
    """一个**定量**方案（一条 KPI + 两条规则）。

    ⚠️ **为什么不用混合方案**
    混合方案的主管评分带 ``manager_review`` 指标 ⇒ 重新评分那一步要传
    ``reviews`` ⇒ 多一层变量。异议分支的重点是「回退 + 重算」，
    用最简单的定量方案**隔离变量**。
    """
    return {
        "code": f"{TAG}_{RUN_SUFFIX}_SCHEME",
        "name": f"{TAG}方案",
        "output_mode": "quantitative",
        "binding": {
            "department_id": BIZ_DEPT_ID,
            "position": "销售",
            "period_type": PERIOD_TYPE,
            "effective_from": "2099-01-01",
            "effective_to": "2099-12-31",
        },
        "metrics": [
            {
                "code": "KPI",
                "name": "业绩指标",
                "type": "number",
                "weight": 100,
                "unit": "单",
                "scoring_method": "ratio",
                "direction": "higher_is_better",
                "scoring_description": "按区间评分",
                "target": 10,
                "rules": [
                    {"level": "B", "min_ratio": 0, "max_ratio": 1, "score": 60},
                    {"level": "A", "min_ratio": 1, "max_ratio": None, "score": 100},
                ],
            }
        ],
        "grades": [
            {"level": "B", "min_score": 0, "max_score": 80},
            {"level": "A", "min_score": 80, "max_score": 100},
        ],
        "fields": [
            {
                "metric_code": "KPI",
                "key": "actual",
                "label": "实际值",
                "stage": "result",
                "type": "number",
                "required": True,
            },
        ],
    }


# ═══════════════════════════════════════════════════════════════════════════
# 主流程：走到「员工提出异议」
# ═══════════════════════════════════════════════════════════════════════════


def run_up_to_dispute(client, people, db) -> tuple[str, str] | None:
    """跑到「员工提出异议」这一步。

    Returns:
        ``(record_id, task_id)``；失败返回 ``None``。
    """
    section("① HR 建方案 → ② 发布")
    version = service.create_version(db, people["hr"], scheme_payload())
    db.commit()
    service.publish(db, people["hr"], version)
    db.commit()
    log("准备", "方案已发布", db.get(type(version), version.id).status == "published")

    section("③ HR 发起考核")
    people["current"] = people["hr"]
    response = client.post(
        "/v1/performance/scheme-tasks",
        json={
            "title": "异议分支考核",
            "period": PERIOD,
            "period_type": PERIOD_TYPE,
            "employee_ids": [EMP_ID],
            "self_review_deadline": SELF_DEADLINE,
            "manager_review_deadline": MANAGER_DEADLINE,
        },
    )
    if response.status_code != 200:
        log("发起", f"失败：{response.status_code} {response.text}", False)
        return None

    task = db.execute(
        select(AssessmentTask)
        .join(AssessmentRecord, AssessmentRecord.task_id == AssessmentTask.id)
        .where(
            AssessmentTask.owner_id == HR_ID,
            AssessmentRecord.employee_id == EMP_ID,
        )
    ).scalars().first()
    if task is None:
        log("发起", "找不到任务", False)
        return None
    snapshot = db.get(ConfigSnapshot, task.snapshot_id) if task.snapshot_id else None
    log("发起", f"任务带快照（{str(task.snapshot_id)[:8]}）", snapshot is not None)

    record = db.execute(
        select(AssessmentRecord).where(AssessmentRecord.task_id == task.id)
    ).scalars().first()
    record_id = record.id
    path = f"/v1/performance/scheme-records/{record_id}"

    section("④ 员工填报并提交自评")
    people["current"] = people["employee"]
    response = client.put(
        path,
        json={
            "revision": 0,
            "values": {"actual": "10"},
            "metric_values": {"KPI": {"actual": 10, "target": 10}},
        },
    )
    if response.status_code != 200:
        log("填报", f"失败：{response.status_code} {response.text}", False)
        return None
    db.expire_all()
    log(
        "填报",
        f"状态 = {db.get(AssessmentRecord, record_id).status}",
        db.get(AssessmentRecord, record_id).status == "self_review_submitted",
    )

    section("⑤ 主管提交评分")
    people["current"] = people["leader"]
    response = client.put(path, json={"revision": _current_revision(db, record_id), "reviews": {}})
    if response.status_code != 200:
        log("评分", f"失败：{response.status_code} {response.text}", False)
        return None
    db.expire_all()
    log(
        "评分",
        f"状态 = {db.get(AssessmentRecord, record_id).status}",
        db.get(AssessmentRecord, record_id).status == "manager_review_submitted",
    )
    score = db.execute(select(Score).where(Score.record_id == record_id)).scalar_one_or_none()
    log("评分", f"score 已落库（total = {score.total if score else None}）", score is not None)
    first_total = score.total if score else None

    section("⑥ 主管记录面谈")
    response = client.post(
        f"{path}/workflow",
        json={
            "revision": _current_revision(db, record_id),
            "action": "interview",
            "note": "首次面谈",
        },
    )
    if response.status_code != 200:
        log("面谈", f"失败：{response.status_code} {response.text}", False)
        return None
    data = response.json()["data"]
    log("面谈", f"phase = {data['phase']}", data["phase"] == "awaiting_employee")

    section("⑦ 员工提出异议 ⚠️ 本脚本的核心")
    people["current"] = people["employee"]
    response = client.post(
        f"{path}/workflow",
        json={
            "revision": _current_revision(db, record_id),
            "action": "dispute",
            "note": "对评分有异议，要求复核",
        },
    )
    if response.status_code != 200:
        log("异议", f"失败：{response.status_code} {response.text}", False)
        return None
    data = response.json()["data"]
    log("异议", f"phase = {data['phase']}", data["phase"] == "disputed")
    log("异议", "记录状态仍为 manager_review_submitted", data["status"] == "manager_review_submitted")

    return record_id, path, first_total  # type: ignore[return-value]


# ═══════════════════════════════════════════════════════════════════════════
# 核心：HR 退回 → 重新评分 → 再定稿
# ═══════════════════════════════════════════════════════════════════════════


def run_reopen_and_rescore(client, people, db, record_id: str, path: str, first_total) -> None:
    """HR 退回 → 主管重新评分 → 面谈 → 确认 → 定稿。"""
    section("⑧ HR 退回重新评分 ⚠️ 关键回退点")

    people["current"] = people["hr"]
    response = client.post(
        f"{path}/workflow",
        json={
            "revision": _current_revision(db, record_id),
            "action": "reopen",
            "note": "异议成立，退回重新评分",
        },
    )
    if response.status_code != 200:
        log("退回", f"失败：{response.status_code} {response.text}", False)
        return
    data = response.json()["data"]
    log("退回", f"phase = {data['phase']}", data["phase"] == "reviewing")

    db.expire_all()
    record = db.get(AssessmentRecord, record_id)
    log(
        "退回",
        f"记录状态退回 = {record.status}",
        record.status == "self_review_submitted",
    )
    log("退回", "confirmed_rating 已清空", record.confirmed_rating is None)

    # 🔴 **关键断言**：旧 score 行必须被删掉（不能留下两条）
    scores = db.execute(select(Score).where(Score.record_id == record_id)).scalars().all()
    log("退回", f"score 行已删除（剩 {len(scores)} 行）", len(scores) == 0)

    # 🔴 **关键断言**：旧分数进了历史（不能丢）
    workflow_state = _workflow_state(db, record_id)
    previous = workflow_state.get("previous_results") or []
    log("退回", f"旧分数存进历史（{len(previous)} 条）", len(previous) >= 1)
    if previous:
        log(
            "退回",
            f"历史分数 = {previous[0].get('total')}（原 {first_total}）",
            previous[0].get("total") == first_total,
        )

    section("⑨ 主管重新评分 ⚠️ 回退后必须还能重新提交")
    people["current"] = people["leader"]
    response = client.put(
        path, json={"revision": _current_revision(db, record_id), "reviews": {}}
    )
    if response.status_code != 200:
        log("重评", f"失败：{response.status_code} {response.text}", False)
        return
    db.expire_all()
    record = db.get(AssessmentRecord, record_id)
    log(
        "重评",
        f"状态 = {record.status}",
        record.status == "manager_review_submitted",
    )

    scores = db.execute(select(Score).where(Score.record_id == record_id)).scalars().all()
    log("重评", f"score 重新落库（{len(scores)} 行）", len(scores) == 1)
    if scores:
        log("重评", f"新分数 = {scores[0].total}", scores[0].total is not None)
        log(
            "重评",
            f"带方案等级 = {(scores[0].trace_json or {}).get('scheme_rating')}",
            bool((scores[0].trace_json or {}).get("scheme_rating")),
        )

    section("⑩ 面谈 → 确认 → 定稿（回退后走第二遍）")
    response = client.post(
        f"{path}/workflow",
        json={
            "revision": _current_revision(db, record_id),
            "action": "interview",
            "note": "二次面谈",
        },
    )
    if response.status_code != 200:
        log("二面谈", f"失败：{response.status_code} {response.text}", False)
        return
    data = response.json()["data"]
    log("二面谈", f"phase = {data['phase']}", data["phase"] == "awaiting_employee")

    people["current"] = people["employee"]
    response = client.post(
        f"{path}/workflow",
        json={
            "revision": _current_revision(db, record_id),
            "action": "acknowledge",
            "note": "复核后确认",
        },
    )
    if response.status_code != 200:
        log("再确认", f"失败：{response.status_code} {response.text}", False)
        return
    data = response.json()["data"]
    log("再确认", f"phase = {data['phase']}", data["phase"] == "awaiting_hr")

    people["current"] = people["hr"]
    response = client.post(
        f"{path}/workflow",
        json={
            "revision": _current_revision(db, record_id),
            "action": "finalize",
            "note": "HR 最终定稿",
        },
    )
    if response.status_code != 200:
        log("再定稿", f"失败：{response.status_code} {response.text}", False)
        return
    data = response.json()["data"]
    log("再定稿", f"phase = {data['phase']}", data["phase"] == "finalized")
    db.expire_all()
    record = db.get(AssessmentRecord, record_id)
    log("再定稿", f"状态 = {record.status}", record.status == "confirmed")
    log("再定稿", f"最终等级 = {record.confirmed_rating}", bool(record.confirmed_rating))


def _workflow_state(db, record_id: str) -> dict:
    """读填报里存的 ``_workflow`` 状态。

    ⚠️ **用 ORM 而不是裸 SQL** —— ``values`` 是 SQLite 保留字。
    """
    from backend.app.domain.scheme_models import SchemeResponse

    row = db.get(SchemeResponse, record_id)
    if row is None:
        return {}
    values = row.values or {}
    return values.get("_workflow", {}) or {}


# ═══════════════════════════════════════════════════════════════════════════
# 反向校验
# ═══════════════════════════════════════════════════════════════════════════


def run_negative_checks(client, people, db, record_id: str, path: str) -> None:
    """验证「该拒绝的确实被拒绝」。

    ⚠️ **重点：异议未解决时不能定稿**
    这是异议分支最容易被绕过的一条 —— 若 ``finalize`` 没检查 phase，
    员工提了异议、HR 直接定稿 ⇒ **异议形同虚设**。
    """
    section("反向校验 · 异议分支")

    # ① 已定稿后不能再退回
    people["current"] = people["hr"]
    response = client.post(
        f"{path}/workflow",
        json={"revision": _current_revision(db, record_id), "action": "reopen", "note": "试图再退一次"},
    )
    log(
        "反向",
        f"定稿后退回被拒（{response.status_code}）",
        response.status_code == 409,
    )

    # ② 员工不能自己定稿
    people["current"] = people["employee"]
    response = client.post(
        f"{path}/workflow",
        json={"revision": _current_revision(db, record_id), "action": "finalize", "note": "员工越权定稿"},
    )
    log(
        "反向",
        f"员工定稿被拒（{response.status_code}）",
        response.status_code == 403,
    )

    # ③ 主管不能退回（只有 HR 能）
    people["current"] = people["leader"]
    response = client.post(
        f"{path}/workflow",
        json={"revision": _current_revision(db, record_id), "action": "reopen", "note": "主管越权退回"},
    )
    log(
        "反向",
        f"主管退回被拒（{response.status_code}）",
        response.status_code == 403,
    )


# ═══════════════════════════════════════════════════════════════════════════
# 入口
# ═══════════════════════════════════════════════════════════════════════════


def main() -> int:
    global db
    db = get_sessionmaker()()

    print("═" * 64)
    print("绩效异议分支 E2E 验证 —— 真实库跑完整条链路")
    print("═" * 64)

    people = build_people(db)
    install_overrides(people, db)
    client = TestClient(app)

    result = run_up_to_dispute(client, people, db)
    if result is None:
        print("\n" + "═" * 64)
        print("结果：失败（未走到异议分支）")
        print("═" * 64)
        return 1
    record_id, path, first_total = result

    run_reopen_and_rescore(client, people, db, record_id, path, first_total)
    run_negative_checks(client, people, db, record_id, path)

    section("汇总")
    print("\n" + "═" * 64)
    if FAILED:
        print(f"结果：失败（通过 {len(PASSED)} 项，存在失败项）")
        print("═" * 64)
        return 1
    print(f"结果：全部通过（{len(PASSED)} 项）")
    print("═" * 64)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
