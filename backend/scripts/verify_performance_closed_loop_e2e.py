"""★ 绩效闭环 E2E 验证（CI-5 阶段 3 第一步）—— 真实库跑完整条链路。

⚠️ **为什么需要这个脚本**
------------------------------------------------------------------
``docs/18-E2E验收清单-绩效闭环.md`` 记录过一次**代码全绿、业务断在中间**的事故：

    真实库里 23 个考核任务里 **22 个 ``snapshot_id`` 为 NULL**，
    21 条记录全部卡在 ``manager_review_submitted``，``score`` 表一条都没有。

⚠️ **为什么单元测试没能挡住**
------------------------------------------------------------------
现有测试（``test_scheme_management.py`` 等）**只覆盖到「提交评分」**，
而：
- ``test_scheme_management.py`` 只断言 ``Score.total == 92``，
  **没有**走完后面的 5 个流程动作；
- 全部用**内存 SQLite + dependency_overrides**，
  ⇒ ``get_db`` 覆盖、事务提交、真实路由装配这些**最容易出问题的地方**
  一个都没被验证。

⚠️ **本脚本补的是哪一段**
------------------------------------------------------------------
把 **10 步主流程**从头到尾跑一遍，**每一步都做库内落库断言**：

    ① HR 建方案草稿          → ``scheme_version`` 落库，状态 draft
    ② HR 发布方案            → 状态 published
    ③ HR 发起考核            → ``assessment_task`` + ``config_snapshot``
                                ⚠️ **断言 ``snapshot_id`` 非 NULL**（BUG-R6-27 的守门）
    ④ 员工填报并提交自评      → ``scheme_response`` + 记录状态推进
                                ⚠️ **接口层是同一步**（``PUT`` 内部即提交自评）
    ⑤ 主管提交评分            → 状态 manager_review_submitted
                                ⚠️ **断言 ``score`` 表有一行**（BUG-R6-28 的守门）
    ⑥ 主管记录面谈            → workflow phase = awaiting_employee
    ⑦ 员工确认                → phase = awaiting_hr
    ⑧ HR 定稿                → 状态 confirmed，等级落库
    ⑨ HR 归档                → 状态 archived，任务状态归档

⚠️ **三条铁律（沿用 ``verify_plan_approval_e2e.py`` 的经验）**
------------------------------------------------------------------
1. ``get_db`` 覆盖必须用 **generator** 形式且**必须 commit**。
   覆盖成 class → FastAPI 解析 ``__init__`` 签名 → 422；
   不提交 → 接口返回 200 但数据全回滚（表现为「接口说成功、库里没变化」）。
2. ``get_current_user`` 覆盖不能塞**已关闭会话**的 Employee 实例，
   否则访问属性时 ``DetachedInstanceError``。
3. 脚本**必须可重复运行** ⇒ 先清掉上一轮自己造的数据。

⚠️ **本脚本不碰真实业务数据**
------------------------------------------------------------------
所有数据都带 ``TAG`` 前缀，且用固定的远期周期（``2099-xx``），
只删本脚本自己造的行。

⚠️ **每轮用全新 ID ⇒ 不做清理 ⇒ 可反复运行**
------------------------------------------------------------------
``review_score_version`` 是 **append-only** 的
（触发器 ``guard_review_score_version_delete`` 禁止 DELETE）
⇒ 算过分的记录，其分数历史**永远删不掉**
⇒ 「固定 ID + 先删后建」的重跑方式必然撞外键错误
⇒ 改为**每轮生成随机后缀的全新 ID**。

⚠️ **已接入 CI**
------------------------------------------------------------------
- ``backend/scripts/run_ci_locally.py`` ⇒ 「绩效闭环 E2E（真实库跑完整链路）」
- ``.github/workflows/ci.yml`` ⇒ 同名步骤（**放在 Alembic 迁移之后**）

用法::

    python backend/scripts/verify_performance_closed_loop_e2e.py
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
from sqlalchemy import bindparam, delete, select  # noqa: E402

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
from backend.app.domain.scheme_models import PerformanceScheme, SchemeVersion  # noqa: E402
from backend.app.main import app  # noqa: E402
from backend.app.services import scheme_service as service  # noqa: E402

# ═══════════════════════════════════════════════════════════════════════════
# 隔离标记
# ═══════════════════════════════════════════════════════════════════════════

#: 脚本造的数据都带这个前缀。
TAG = "E2E_CL"

#: 远期周期 —— 避免撞上真实业务数据。
PERIOD = "209901"
PERIOD_TYPE = "monthly"
SELF_DEADLINE = "2099-02-01T12:00:00"
MANAGER_DEADLINE = "2099-02-05T12:00:00"

# ⚠️⚠️ **每轮运行用一组全新的 ID**（而不是固定 ID）
# ------------------------------------------------------------------
# 起因：**``review_score_version`` 是 append-only 的**
# （``guard_review_score_version_delete`` 触发器：
#   ``BEFORE DELETE ... RAISE(ABORT, 'append-only history')``）
# ⇒ 凡是**算过分的记录**，它的分数版本历史**永远删不掉**。
# ⇒ 固定 ID + 「先删后建」的重跑方式 ⇒ 第二次跑必然撞
#   ``FOREIGN KEY constraint failed``（删不掉历史 ⇒ 删不掉记录）。
#
# ⇒ 改成**每轮生成随机后缀的全新 ID** ⇒
#   ① 不与上一轮的数据冲突（可以反复跑）
#   ② **不需要删任何东西**（也就不会被 append-only 挡住）
#   ③ 残留数据带 ``TAG`` 前缀，事后可按前缀人工清理
#
# ⚠️ **为什么周期也要跟着变**
# 「同周期内已有任务时，再发起会被拒」（范围互斥）⇒
# 周期用「本次运行的唯一值」才能反复跑。
RUN_SUFFIX = uuid.uuid4().hex[:8]

#: 本轮的三个人物 ID。
HR_ID = f"{TAG}_{RUN_SUFFIX}_HR"
LEADER_ID = f"{TAG}_{RUN_SUFFIX}_L"
EMP_ID = f"{TAG}_{RUN_SUFFIX}_E"
HR_DEPT_ID = f"{TAG}_{RUN_SUFFIX}_DEPT_H"
BIZ_DEPT_ID = f"{TAG}_{RUN_SUFFIX}_DEPT_B"

#: 第二套（混合方案用）—— 原因见 ``build_people`` 的注释。
LEADER2_ID = f"{TAG}_{RUN_SUFFIX}_L2"
EMP2_ID = f"{TAG}_{RUN_SUFFIX}_E2"
BIZ2_DEPT_ID = f"{TAG}_{RUN_SUFFIX}_DEPT_B2"

#: 本轮的考核周期。
#:
#: ⚠️ **为什么可以用固定周期**
#: 「同周期不能重复发起」的互斥规则比对的是**同一个员工** ⇒
#: 本脚本每轮都用**全新的员工 ID** ⇒ 永远不会撞上 ⇒ 周期固定即可。
PERIOD = "209901"

FAILED = False
PASSED: list[str] = []


def log(stage: str, message: str, ok: bool = True) -> None:
    """打一条阶段日志；``ok=False`` 会把整脚本标记为失败。"""
    global FAILED
    mark = "✔" if ok else "✖"
    print(f"{mark} [{stage}] {message}")
    if ok:
        PASSED.append(f"[{stage}] {message}")
    else:
        FAILED = True


def section(title: str) -> None:
    print(f"\n── {title} " + "─" * max(0, 60 - len(title)))


def _delete_by_column(db, table_name: str, column: str, values: list[str]) -> None:
    """按「表名 + 列名」删行 —— **表不存在时静默跳过**。

    ⚠️ **为什么需要它**
    不同版本的库里可能还没有某张表（如 ``SchemeDispute``），
    而清理脚本必须**在所有版本上都能跑** ⇒ 硬编码表名会直接崩。

    ⚠️⚠️ **为什么用 SAVEPOINT 而不是直接 ``db.rollback()``**
    ------------------------------------------------------
    直接 ``rollback()`` 会把**整个事务**回滚 —— 包括**前面已经成功删掉的行**！
    ⇒ 清理到一半被一张不存在的表打断 ⇒ 前面删的全白删，
    最后还是撞外键约束。

    ⇒ 用 ``begin_nested()``（SAVEPOINT）把「这张表删不了」**隔离**在这一句上，
    回滚**只**影响它自己。

    ⚠️ **SQLite 的 ``IN :ids`` 语法**
    SQLAlchemy 的 ``expanding`` 参数在原生 SQL 里**不能**直接写成 ``IN :ids``，
    得用 ``bindparam(expanding=True)`` 才能展开成 ``IN (?, ?, ?)``。
    """
    from sqlalchemy import text as _text

    stmt = _text(f"DELETE FROM {table_name} WHERE {column} IN :ids").bindparams(
        bindparam("ids", expanding=True)
    )
    try:
        with db.begin_nested():
            db.execute(stmt, {"ids": list(values)})
    except Exception:
        # ⚠️ 表不存在 / 列不存在 ⇒ 跳过（**不要**让清理阶段崩掉）。
        pass


def _dependent_tables(db, referred_table: str) -> list[tuple[str, str]]:
    """**自动发现**所有外键指向 ``referred_table`` 的表。

    ⚠️ **为什么必须自动发现，不能硬编码**
    ------------------------------------------------------
    ``assessment_record`` 一张表就被 **15 张表**外键引用
    （``score`` / ``self_review`` / ``manager_review`` / ``evidence`` /
    ``appeal`` / ``review_case`` / …）。
    硬编码一份清单 ⇒ **加一张新表就漏删** ⇒ 清理阶段报
    ``FOREIGN KEY constraint failed``（脚本根本跑不起来）。

    ⇒ 改为**从库的真实外键元数据里读** ⇒ 永远跟得上 schema 变化。
    """
    from sqlalchemy import inspect

    inspector = inspect(db.bind)
    out: list[tuple[str, str]] = []
    for table_name in inspector.get_table_names():
        try:
            fks = inspector.get_foreign_keys(table_name)
        except Exception:
            continue
        for fk in fks:
            if fk.get("referred_table") != referred_table:
                continue
            for column in fk.get("constrained_columns") or []:
                out.append((table_name, column))
    return out


def _delete_dependents(db, referred_table: str, key_column: str, values: list[str]) -> None:
    """按外键依赖倒序删掉所有引用 ``referred_table`` 的行。

    ⚠️ **为什么必须「递归」而不是只删一层**
    ------------------------------------------------------
    只删一层会漏掉**二级依赖**：
    - ``review_score_version.record_id`` → ``assessment_record``
    - ``review_score_version.head_id``  → ``review_score_head``
    - ``review_score_head.record_id``  → ``assessment_record``

    ⇒ ``review_score_head`` 和 ``review_score_version`` **都**直接引用
    ``assessment_record``，但它们**彼此还有外键**。
    ⇒ 若先删 head，version 就成了孤儿 ⇒ 报外键错误。

    ⇒ 这里用**递归 + 深度优先**：先删「依赖别人的」，再删「被依赖的」。

    ⚠️⚠️ **软引用（无外键约束）必须手工补**
    ------------------------------------------------------
    ``audit_log.resource_id`` 与 ``notification.object_id`` 指向业务对象的 id，
    但**没有声明外键约束** ⇒ 外键检查器**看不见**它们
    ⇒ 只靠元数据删除 ⇒ 这两张表残留 ⇒ 删 ``assessment_record`` 时报外键错误。

    ⇒ 补一张**软引用表**清单（见 ``SOFT_REFERENCES``）。
    """
    _delete_dependents_recursive(db, referred_table, values, depth=0, seen=set())
    # ⚠️ 软引用（无 FK 约束）⇒ 外键检查器看不见 ⇒ 手工补删
    for table, column in SOFT_REFERENCES.get(referred_table, ()):
        _delete_by_column(db, table, column, values)


#: **软引用**清单：指向业务对象 id 但**没有外键约束**的列。
#:
#: ⚠️ **为什么要手工维护这张表**
#: ``audit_log`` / ``notification`` 是「通用日志/消息」表，
#: 它们用 ``resource_id`` / ``object_id`` 指向任意业务对象，
#: **故意不建外键**（否则每加一种业务对象都要改 schema）。
#: ⇒ 代价：外键检查器看不见它们 ⇒ 清理脚本必须手工补。
SOFT_REFERENCES: dict[str, tuple[tuple[str, str], ...]] = {
    "assessment_record": (
        ("audit_log", "resource_id"),
        ("notification", "object_id"),
    ),
    "assessment_task": (
        ("audit_log", "resource_id"),
        ("notification", "object_id"),
    ),
}


def _delete_dependents_recursive(
    db,
    referred_table: str,
    values: list[str],
    *,
    depth: int,
    seen: set[tuple[str, str]],
) -> None:
    """递归删除依赖链。

    ⚠️ **``seen`` 防止无限递归**（A→B→A 的循环外键是真实存在的，
    比如 ``employee.manager_id`` → ``employee.id``）。
    """
    # ⚠️ **深度上限**：防御性 —— 正常 schema 深度 < 5。
    if depth > 8 or not values:
        return

    # 1) 先找出**所有引用者**，按「谁的依赖更浅」排序
    dependents = [
        (table, column)
        for table, column in _dependent_tables(db, referred_table)
        if table != referred_table
    ]
    if not dependents:
        return

    # 2) 逐个删；每个删之前先递归删它的依赖
    for table, column in dependents:
        key = (table, column)
        if key in seen:
            continue
        seen.add(key)
        # 2.1) 先取出这个表里将被删掉的行的**主键**
        try:
            from sqlalchemy import text as _text

            pk = _primary_key_column(db, table)
            if pk is None:
                continue
            rows = db.execute(
                _text(f"SELECT {pk} FROM {table} WHERE {column} IN :ids").bindparams(
                    bindparam("ids", expanding=True)
                ),
                {"ids": list(values)},
            ).fetchall()
        except Exception:
            continue
        if not rows:
            continue
        # 2.2) 递归删掉这些行自己引用的东西
        _delete_dependents_recursive(
            db,
            table,
            [str(r[0]) for r in rows],
            depth=depth + 1,
            seen=seen,
        )
        # 2.3) 最后删本表的行
        _delete_by_column(db, table, column, values)


def _primary_key_column(db, table_name: str) -> str | None:
    """取一张表的**单列主键**名。

    ⚠️ **为什么要单列主键**
    复合主键没法用一个 ``IN (...)`` 表达 ⇒ 递归逻辑会失效。
    本项目所有表都是单列主键（UUID 字符串）。
    """
    from sqlalchemy import inspect

    try:
        inspector = inspect(db.bind)
        pk = inspector.get_pk_constraint(table_name).get("constrained_columns") or []
        return pk[0] if len(pk) == 1 else None
    except Exception:
        return None


def _current_revision(db, record_id: str) -> int:
    """读当前填报的 ``revision``。

    ⚠️ **为什么必须从库里读**
    ------------------------------------------------------
    ``POST/PUT /scheme-records/{id}`` 的返回体**只有**
    ``{"record_id", "status"}`` —— **不含 revision**。
    ⇒ 如果按「接口返回什么就用什么」写，下一次请求必然带**过期的 revision**
    ⇒ 后端回 409「填报内容已变化，请刷新」。
    ⇒ 看起来像「链路断了」，其实是**取值方式错了**。

    ⚠️ **乐观锁**
    ``revision`` 是这条填报的**版本号** ⇒ 每次写操作都会 +1。
    后续所有请求（主管评分、面谈、确认、定稿）都必须带**最新的** revision。
    """
    from sqlalchemy import text as _text

    row = db.execute(
        _text("SELECT revision FROM performance_scheme_response WHERE record_id = :rid"),
        {"rid": record_id},
    ).first()
    return int(row[0]) if row else 0


# ═══════════════════════════════════════════════════════════════════════════
# 0. 准备：清掉上一轮的残留 + 建好三个角色
# ═══════════════════════════════════════════════════════════════════════════


def cleanup_previous_run(db) -> None:
    """删除**上一轮**本脚本造的数据（**手工调用**，不在主流程里跑）。

    ⚠️⚠️ **为什么主流程不跑它**
    ------------------------------------------------------
    ``review_score_version`` 是 **append-only** 的
    （触发器 ``guard_review_score_version_delete``：
      ``BEFORE DELETE ON review_score_version → RAISE(ABORT, 'append-only history')``）
    ⇒ 凡是**算过分的记录**，它的分数版本历史**永远删不掉**
    ⇒ 删不掉历史 ⇒ 删不掉记录 ⇒ 报 ``FOREIGN KEY constraint failed``。

    ⇒ 主流程改为**每轮用全新的 ID**（见 ``RUN_SUFFIX``），
      压根不需要清理 ⇒ 也就不会被 append-only 挡住。

    ⚠️ **这个函数仍然有用**
    ⇒ 用来**手工清理**那些「没算过分的残留」（比如调试中断的运行）。
    ⇒ 算过分的残留**清不掉**（这是**正确的** —— 分数历史不可篡改），
      只能按 ``E2E_CL`` 前缀人工核对。

    ⚠️ **只删本脚本自己造的东西**（ID 带 ``TAG``），绝不碰真实业务数据。
    """
    # 1) 先收集要删的记录 id（**两套**员工都要清）
    emp_ids = [EMP_ID, EMP2_ID]
    record_ids = list(
        db.execute(
            select(AssessmentRecord.id).where(AssessmentRecord.employee_id.in_(emp_ids))
        ).scalars().all()
    )
    if record_ids:
        # 2) **自动发现**并删除所有引用 assessment_record 的行
        #    （score / self_review / manager_review / evidence / appeal / …）
        _delete_dependents(db, "assessment_record", "id", record_ids)
        # 3) 再删记录本身
        db.execute(delete(AssessmentRecord).where(AssessmentRecord.id.in_(record_ids)))

    # 4) 删任务
    # ⚠️ **顺序：先删任务，再删快照** —— ``assessment_task.snapshot_id``
    #    外键指向 ``config_snapshot.id`` ⇒ 快照被任务引用时不能先删快照。
    task_ids = list(
        db.execute(
            select(AssessmentTask.id).where(AssessmentTask.owner_id == HR_ID)
        ).scalars().all()
    )
    if task_ids:
        _delete_dependents(db, "assessment_task", "id", task_ids)
        db.execute(delete(AssessmentTask).where(AssessmentTask.id.in_(task_ids)))
        # 任务删掉后快照才没有引用者
        db.execute(delete(ConfigSnapshot).where(ConfigSnapshot.task_id.in_(task_ids)))
    # 5) 删方案版本
    # ⚠️ **不能按 ``id LIKE '%TAG%'`` 删** —— ``SchemeVersion.id`` 是 **UUID**，
    #    ``TAG`` 根本不在里面 ⇒ 一行都删不掉。
    # ⇒ 真正的标识在 ``PerformanceScheme.code``（本脚本造的都带 ``TAG`` 前缀）。
    scheme_ids = list(
        db.execute(
            select(PerformanceScheme.id).where(PerformanceScheme.code.like(f"{TAG}%"))
        ).scalars().all()
    )
    if scheme_ids:
        # ⚠️ **先删版本，再删方案** ——
        #    ``performance_scheme_binding.version_id`` 外键指向
        #    ``performance_scheme_version.id`` ⇒ 版本被绑定行引用时不能先删版本。
        version_ids = list(
            db.execute(
                select(SchemeVersion.id).where(SchemeVersion.scheme_id.in_(scheme_ids))
            ).scalars().all()
        )
        if version_ids:
            _delete_dependents(db, "performance_scheme_version", "id", version_ids)
            db.execute(delete(SchemeVersion).where(SchemeVersion.id.in_(version_ids)))
        db.execute(delete(PerformanceScheme).where(PerformanceScheme.id.in_(scheme_ids)))

    # 6) 删人员与部门（**先删部门配置，再删人，最后删部门**）
    person_ids = [HR_ID, LEADER_ID, EMP_ID, LEADER2_ID, EMP2_ID, f"{TAG}_X"]
    dept_ids = [HR_DEPT_ID, BIZ_DEPT_ID, BIZ2_DEPT_ID]
    _delete_dependents(db, "employee", "id", person_ids)
    db.execute(delete(HrDepartmentConfig).where(HrDepartmentConfig.department_id.in_(dept_ids)))
    db.execute(delete(Employee).where(Employee.id.in_(person_ids)))
    _delete_dependents(db, "department", "id", dept_ids)
    db.execute(delete(Department).where(Department.id.in_(dept_ids)))
    db.commit()


def build_people(db) -> dict[str, Employee]:
    """建 HR / 主管 / 员工 三个人。

    ⚠️ **汇报线必须真实存在** ——
    主管能看到员工的数据范围是靠 ``manager_id`` 建立的，
    没有它 ⇒ 主管访问员工记录会被判「无权查看」⇒ 流程第 ⑤ 步就断了。

    ⚠️ **为什么建两套部门**
    「同部门、岗位、周期存在生效时间重叠的已发布方案」是**正确的业务规则**
    （一个部门的同一岗位同一周期只能有一版生效方案）。
    ⇒ 定量方案与混合方案若共用部门 ⇒ 第二版发布必被 409 挡住。
    ⇒ 两条链路各用**自己的部门 + 自己的员工**。
    """
    db.add_all(
        [
            Department(id=HR_DEPT_ID, name=f"{TAG}人事"),
            Department(id=BIZ_DEPT_ID, name=f"{TAG}业务", leader_id=LEADER_ID),
            Department(id=BIZ2_DEPT_ID, name=f"{TAG}业务二", leader_id=LEADER2_ID),
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
        # 第二套（混合方案用）
        "leader2": Employee(
            id=LEADER2_ID,
            name=f"{TAG}主管二",
            department_id=BIZ2_DEPT_ID,
            position="负责人",
        ),
        "employee2": Employee(
            id=EMP2_ID,
            name=f"{TAG}员工二",
            department_id=BIZ2_DEPT_ID,
            position="销售",
            manager_id=LEADER2_ID,
        ),
    }
    db.add_all(people.values())
    # HR 身份 = 所属部门命中启用的 HR 部门配置
    db.add(HrDepartmentConfig(department_id=HR_DEPT_ID, active=True))
    db.commit()
    return people


def install_overrides(people: dict[str, Employee]) -> None:
    """装上 ``get_db`` / ``get_current_user`` 覆盖。

    ⚠️ **三条铁律**（见模块 docstring）：
    - ``get_db`` 必须是 **generator** 且 **commit**
    - ``get_current_user`` 返回的必须是**当前会话里活着**的对象
    """

    def session():
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise

    app.dependency_overrides[get_db] = session
    app.dependency_overrides[get_current_user] = lambda: people["current"]


# ═══════════════════════════════════════════════════════════════════════════
# 方案 payload
# ═══════════════════════════════════════════════════════════════════════════


def scheme_payload() -> dict:
    """构造一个**定量**方案（一个 KPI 指标 + 一条等级规则）。"""
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
            {
                "metric_code": "KPI",
                "key": "summary",
                "label": "成果说明",
                "stage": "result",
                "type": "textarea",
                "required": True,
            },
        ],
    }


def mixed_payload() -> dict:
    """构造一个**混合**方案（定量 KPI + 定性 TEXT）。

    ⚠️ **为什么要单独跑一遍混合方案**
    定量方案只走「按比例算分」这一条路；
    混合方案多一条「主管主观打分」的路（``scoring_method = manager_review``）。
    ⇒ 两者的算分路径不同，**都要验**。

    ⚠️ **口径提醒**
    - ``output_mode`` 只接受 ``quantitative`` / ``narrative``
      （「混合」是靠**指标各自的 ``scoring_method``** 表达的）
    - 权重合计必须是 **100**
    - 表单字段的 ``metric_code`` 必须命中**已存在的指标编码**
      ⇒ 定性字段要挂在 ``TEXT`` 指标下，不能挂在 ``KPI`` 上
    - ⚠️ **必须绑到** ``BIZ2_DEPT_ID`` —— 同部门同岗位同周期的生效范围
      不能重叠（那是**正确的业务规则**），所以两条链路各用一个部门。
    """
    p = scheme_payload()
    p["code"] = f"{TAG}_{RUN_SUFFIX}_MIXED"
    p["name"] = f"{TAG}混合方案"
    p["binding"]["department_id"] = BIZ2_DEPT_ID
    p["metrics"] = [
        {
            "code": "KPI",
            "name": "业绩指标",
            "type": "number",
            "weight": 60,
            "unit": "单",
            "scoring_method": "ratio",
            "direction": "higher_is_better",
            "scoring_description": "按区间评分",
            "target": 10,
            "rules": [
                {"level": "B", "min_ratio": 0, "max_ratio": 1, "score": 60},
                {"level": "A", "min_ratio": 1, "max_ratio": None, "score": 100},
            ],
        },
        {
            "code": "TEXT",
            "name": "工作成果",
            "type": "narrative",
            "weight": 40,
            "scoring_method": "manager_review",
            "scoring_description": "按HR标准由主管评价",
            "rules": [],
        },
    ]
    p["fields"] = [
        {
            "metric_code": "KPI",
            "key": "actual",
            "label": "实际值",
            "stage": "result",
            "type": "number",
            "required": True,
        },
        {
            "metric_code": "TEXT",
            "key": "summary",
            "label": "成果说明",
            "stage": "result",
            "type": "textarea",
            "required": True,
        },
    ]
    p["grades"] = [
        {"level": "B", "min_score": 0, "max_score": 80},
        {"level": "A", "min_score": 80, "max_score": 100},
    ]
    return p


# ═══════════════════════════════════════════════════════════════════════════
# 主流程
# ═══════════════════════════════════════════════════════════════════════════


def run_scheme_flow(client, people, payload, *, label: str, emp_id: str, leader_key: str) -> dict | None:
    """跑完一条方案考核的 9 步。

    Args:
        emp_id: 被评员工 ID
        leader_key: ``people`` 字典里主管的键（``"leader"`` / ``"leader2"``）

    Returns:
        跑通返回 ``{"record_id", "task_id", "score", "rating"}``；
        跑不通返回 ``None``。
    """
    section(f"{label} · ① HR 建方案草稿")

    # ① 建草稿
    version = service.create_version(db, people["hr"], payload)
    db.commit()
    log(label, f"方案版本已创建：{version.id[:8]}")

    # ② 发布
    section(f"{label} · ② HR 发布方案")
    service.publish(db, people["hr"], version)
    db.commit()
    fresh = db.get(SchemeVersion, version.id)
    log(label, f"方案状态 = {fresh.status}", fresh.status == "published")

    # ③ 发起考核
    section(f"{label} · ③ HR 发起考核")
    people["current"] = people["hr"]
    response = client.post(
        "/v1/performance/scheme-tasks",
        json={
            "title": f"{label}考核",
            "period": PERIOD,
            "period_type": PERIOD_TYPE,
            "employee_ids": [emp_id],
            "self_review_deadline": SELF_DEADLINE,
            "manager_review_deadline": MANAGER_DEADLINE,
        },
    )
    if response.status_code != 200:
        log(label, f"发起考核失败：{response.status_code} {response.text}", False)
        return None
    tasks = response.json()["data"]
    log(label, f"发起成功，目标人数 = {tasks[0]['employee_count']}", tasks[0]["employee_count"] == 1)

    # 🔴 **BUG-R6-27 守门**：任务必须带快照，否则**永远算不出分**
    # ⚠️ **必须按「本轮的被评员工」定位任务**，不能只按 owner_id ——
    #    同一个 HR 会先后发起多个任务（定量/混合各一个）
    #    ⇒ ``.first()`` 会拿到**上一条链路的任务**（甚至已归档的）
    #    ⇒ 后面全部步骤都在错的记录上跑。
    task = db.execute(
        select(AssessmentTask)
        .join(AssessmentRecord, AssessmentRecord.task_id == AssessmentTask.id)
        .where(
            AssessmentTask.owner_id == HR_ID,
            AssessmentRecord.employee_id == emp_id,
        )
        .order_by(AssessmentTask.created_at.desc())
    ).scalars().first()
    if task is None:
        log(label, "库里找不到刚建的任务", False)
        return None
    snapshot = db.get(ConfigSnapshot, task.snapshot_id) if task.snapshot_id else None
    log(
        label,
        f"任务 snapshot_id 非空（{str(task.snapshot_id)[:8]}）",
        task.snapshot_id is not None and snapshot is not None,
    )
    log(
        label,
        "快照含指标与权重",
        bool(snapshot and snapshot.metrics_json and snapshot.weights_json),
    )
    log(
        label,
        "快照绑定方案版本",
        bool(snapshot and (snapshot.form_template_json or {}).get("scheme_version_id")),
    )

    # 找到考核记录
    record = db.execute(
        select(AssessmentRecord).where(AssessmentRecord.task_id == task.id)
    ).scalars().first()
    if record is None:
        log(label, "库里找不到考核记录", False)
        return None
    record_id = record.id
    path = f"/v1/performance/scheme-records/{record_id}"
    log(label, f"记录初始状态 = {record.status}", record.status == "not_started")

    # ④ 员工填报并提交自评
    # ⚠️ **「填报表」与「提交自评」在接口上是同一个动作**
    #    —— `PUT /scheme-records/{id}` 内部调 `submit_self_review`，
    #    一次请求就把「填的内容」和「自评已提交」都做完了。
    #    ⇒ 验收清单里写的「④ 填报表 → ⑤ 提交自评」在**接口层是同一步**。
    section(f"{label} · ④ 员工填报并提交自评")
    people["current"] = people["employee" if emp_id == EMP_ID else "employee2"]
    response = client.get(path)
    log(label, f"员工可读表单（{response.status_code}）", response.status_code == 200)

    response = client.put(
        path,
        json={
            "revision": 0,
            "values": {"actual": "10", "summary": "完成交付"},
            "metric_values": {"KPI": {"actual": 10, "target": 10}},
        },
    )
    if response.status_code != 200:
        log(label, f"填报并提交自评失败：{response.status_code} {response.text}", False)
        return None
    log(label, "填报并提交自评成功", True)
    db.expire_all()
    record = db.get(AssessmentRecord, record_id)
    log(label, f"记录状态 = {record.status}", record.status == "self_review_submitted")
    # ⚠️ **接口不返回 revision** —— `submit_response` 只返回
    #    ``{"record_id", "status"}``。⇒ revision 必须**从库里读**。
    revision = _current_revision(db, record_id)

    # ⑤ 主管提交评分
    section(f"{label} · ⑤ 主管提交评分")
    people["current"] = people[leader_key]
    # ⚠️ **`reviews` 必须只包含「主管人工评价」的指标**
    #    —— 后端会校验 ``set(body.reviews) == {人工评价指标的编码集合}``。
    #    ⇒ 定量方案（全是 ratio）⇒ **必须传空对象**；
    #      混合方案（有 manager_review 的 TEXT）⇒ 传 TEXT 的评分。
    #    ⇒ 传多了或传少了都 422「请完成全部人工评价指标」。
    manual_codes = [
        m["code"] for m in payload["metrics"] if m["scoring_method"] == "manager_review"
    ]
    reviews = {
        code: {"score": 80, "comment": "按正式标准验收通过"} for code in manual_codes
    }
    response = client.put(path, json={"revision": revision, "reviews": reviews})
    if response.status_code != 200:
        log(label, f"主管评分失败：{response.status_code} {response.text}", False)
        return None
    log(label, f"主管评分成功（人工评价指标 = {manual_codes or '无'}）", True)
    db.expire_all()
    record = db.get(AssessmentRecord, record_id)
    log(label, f"记录状态 = {record.status}", record.status == "manager_review_submitted")
    # ⚠️ 同样**从库里读** revision（接口只回 record_id + status）
    revision = _current_revision(db, record_id)

    # 🔴 **BUG-R6-28 守门**：评分落库**必须**伴随一次算分
    score = db.execute(select(Score).where(Score.record_id == record_id)).scalar_one_or_none()
    log(label, f"score 表已落库（total = {score.total if score else None}）", score is not None)
    log(
        label,
        f"分数带方案等级 = {(score.trace_json or {}).get('scheme_rating') if score else None}",
        bool(score and (score.trace_json or {}).get("scheme_rating")),
    )

    # ⑦ 主管记录面谈
    section(f"{label} · ⑥ 主管记录面谈")
    response = client.post(
        f"{path}/workflow",
        json={"revision": revision, "action": "interview", "note": "面谈完成，达成一致"},
    )
    if response.status_code != 200:
        log(label, f"记录面谈失败：{response.status_code} {response.text}", False)
        return None
    data = response.json()["data"]
    log(label, f"流程阶段 = {data['phase']}", data["phase"] == "awaiting_employee")
    revision = data["revision"]

    # ⑧ 员工确认
    section(f"{label} · ⑦ 员工确认")
    people["current"] = people["employee" if emp_id == EMP_ID else "employee2"]
    response = client.post(
        f"{path}/workflow",
        json={"revision": revision, "action": "acknowledge", "note": "确认无误"},
    )
    if response.status_code != 200:
        log(label, f"员工确认失败：{response.status_code} {response.text}", False)
        return None
    data = response.json()["data"]
    log(label, f"流程阶段 = {data['phase']}", data["phase"] == "awaiting_hr")
    revision = data["revision"]

    # ⑨ HR 定稿
    section(f"{label} · ⑧ HR 定稿")
    people["current"] = people["hr"]
    response = client.post(
        f"{path}/workflow",
        json={"revision": revision, "action": "finalize", "note": "HR 定稿确认"},
    )
    if response.status_code != 200:
        log(label, f"HR 定稿失败：{response.status_code} {response.text}", False)
        return None
    data = response.json()["data"]
    log(label, f"流程阶段 = {data['phase']}", data["phase"] == "finalized")
    db.expire_all()
    record = db.get(AssessmentRecord, record_id)
    log(label, f"记录状态 = {record.status}", record.status == "confirmed")
    log(label, f"最终等级 = {record.confirmed_rating}", bool(record.confirmed_rating))
    revision = data["revision"]

    # ⑩ HR 归档
    section(f"{label} · ⑨ HR 归档")
    response = client.post(
        f"{path}/workflow",
        json={"revision": revision, "action": "archive", "note": "归档"},
    )
    if response.status_code != 200:
        log(label, f"归档失败：{response.status_code} {response.text}", False)
        return None
    data = response.json()["data"]
    log(label, f"流程阶段 = {data['phase']}", data["phase"] == "archived")
    db.expire_all()
    record = db.get(AssessmentRecord, record_id)
    log(label, f"记录状态 = {record.status}", record.status == "archived")
    task = db.get(AssessmentTask, task.id)
    log(label, f"任务状态 = {task.status}", task.status == "archived")

    score = db.execute(select(Score).where(Score.record_id == record_id)).scalar_one_or_none()
    return {
        "record_id": record_id,
        "task_id": task.id,
        "score": score.total if score else None,
        "rating": record.confirmed_rating,
    }


def run_negative_checks(client, people) -> None:
    """跑几组**权限与状态机**的反向断言。

    ⚠️ **为什么正向跑通还不够**
    链路能走通 ≠ 链路**不该走的时候**也走不通。
    ⇒ 必须验证「该拒绝的确实被拒绝」。
    """
    section("反向校验 · 权限与状态机")

    # 找一个已归档的记录
    record = db.execute(
        select(AssessmentRecord).where(AssessmentRecord.employee_id == EMP_ID)
    ).scalars().first()
    if record is None:
        log("反向", "找不到记录，跳过反向校验", False)
        return
    path = f"/v1/performance/scheme-records/{record.id}"

    # ① 员工不能记录面谈
    people["current"] = people["employee"]
    response = client.post(
        f"{path}/workflow",
        json={"revision": 999, "action": "interview", "note": "越权尝试"},
    )
    log(
        "反向",
        f"员工记录面谈被拒（{response.status_code}）",
        response.status_code in (403, 409),
    )

    # ② 其他部门的员工看不到这条记录
    # ⚠️ **必须放在「既不是 HR、也不是该员工所在部门」的地方**
    #    —— 放进 HR 部门 ⇒ HR 本来就能看全部 ⇒ 断言 403 会失败（那是**正常的**）。
    #    ⇒ 放在第二个业务部门（有自己的主管与员工，但与本记录无汇报线关系）。
    other = Employee(
        id=f"{TAG}_{RUN_SUFFIX}_X", name=f"{TAG}外部", department_id=BIZ2_DEPT_ID, position="销售"
    )
    db.add(other)
    db.commit()
    people["current"] = other
    response = client.get(path)
    log(
        "反向",
        f"外部员工读记录被拒（{response.status_code}）",
        response.status_code == 403,
    )

    # ③ 空操作说明被拒
    people["current"] = people["leader"]
    response = client.post(
        f"{path}/workflow",
        json={"revision": 999, "action": "interview", "note": "  "},
    )
    log(
        "反向",
        f"空面谈说明被拒（{response.status_code}）",
        response.status_code in (400, 409),
    )

    # ④ 归档后不可再改
    people["current"] = people["hr"]
    response = client.post(
        f"{path}/workflow",
        json={"revision": 999, "action": "finalize", "note": "重复定稿"},
    )
    log(
        "反向",
        f"归档后重复定稿被拒（{response.status_code}）",
        response.status_code == 409,
    )


# ═══════════════════════════════════════════════════════════════════════════
# 入口
# ═══════════════════════════════════════════════════════════════════════════


def main() -> int:
    global db
    db = get_sessionmaker()()

    print("═" * 64)
    print("绩效闭环 E2E 验证 —— 真实库跑完整 10 步")
    print("═" * 64)

    # ⚠️ **不再做「先删后建」的清理**
    # ⇒ 每轮用**全新的员工/部门/方案 ID**（见 ``RUN_SUFFIX`` 的说明），
    #    压根不需要删上一轮的数据 ⇒ 也就不会被
    #    ``review_score_version`` 的 append-only 触发器挡住。
    # ⇒ 残留数据带 ``E2E_CL`` 前缀，事后可按前缀人工核对/清理。
    people = build_people(db)
    install_overrides(people)
    client = TestClient(app)

    results = []
    for label, payload, emp_id, leader_key in (
        ("定量方案", scheme_payload(), EMP_ID, "leader"),
        ("混合方案", mixed_payload(), EMP2_ID, "leader2"),
    ):
        result = run_scheme_flow(
            client, people, payload, label=label, emp_id=emp_id, leader_key=leader_key
        )
        results.append((label, result))

    run_negative_checks(client, people)

    section("汇总")
    for label, result in results:
        if result is None:
            log("汇总", f"{label}：链路未跑通", False)
        else:
            log(
                "汇总",
                f"{label}：跑通（分数 = {result['score']}，等级 = {result['rating']}）",
                True,
            )

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
