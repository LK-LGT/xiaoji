"""P0-3：问答授权决策器的测试。

⚠️ 这个文件守的是**问答链路的授权边界**：
   - 同部门同级别 → 允许
   - 跨部门 / 跨级 / 上一级 → 拒绝
   - 写操作意图 → 拦截并给引导
   - 越权 → 高危审计留痕
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app.domain.models import (
    AssessmentRecord,
    AssessmentTask,
    Base,
    Department,
    Employee,
    EmployeeDepartment,
    HrDepartmentConfig,
)
from backend.app.services.qa_authorization import (
    AuthorizationDecision,
    authorize_qa_subject,
    detect_write_action,
    resolve_scope,
)


@pytest.fixture()
def db() -> Session:
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def _emp(
    db: Session,
    emp_id: str,
    name: str,
    dept: str | None,
    manager: str | None = None,
) -> Employee:
    e = Employee(
        id=emp_id,
        name=name,
        department_id=dept,
        manager_id=manager,
        employment_status="active",
        account_status="enabled",
    )
    db.add(e)
    db.flush()
    return e


@pytest.fixture()
def seeded(db: Session) -> Session:
    db.add_all([
        Department(id="D_ROOT", name="总部", active=True),
        Department(id="D_SALES", name="销售部", active=True),
        Department(id="D_MKT", name="市场部", active=True),
        Department(id="D_HR", name="人力资源部", active=True),
    ])
    db.flush()
    _emp(db, "BOSS", "总裁", "D_ROOT", None)
    _emp(db, "M_SALES", "销售经理", "D_SALES", "BOSS")
    _emp(db, "E_A", "销售甲", "D_SALES", "M_SALES")
    _emp(db, "E_B", "销售乙", "D_SALES", "M_SALES")
    _emp(db, "E_C", "销售丙", "D_SALES", "E_B")
    _emp(db, "E_X", "市场甲", "D_MKT", "M_SALES")
    _emp(db, "E_HR", "HR", "D_HR", "BOSS")
    db.add(HrDepartmentConfig(department_id="D_HR", active=True))
    db.commit()
    return db


# ---------------------------------------------------------------------------
# 授权判定
# ---------------------------------------------------------------------------


def test_self_allowed(seeded: Session) -> None:
    """本人放行。"""
    asker = seeded.get(Employee, "E_A")
    subject = seeded.get(Employee, "E_A")
    decision = authorize_qa_subject(seeded, asker, subject)
    assert decision.allowed is True
    assert decision.subject_type == "self"


def test_same_department_same_level_allowed(seeded: Session) -> None:
    """同部门同级别放行。"""
    asker = seeded.get(Employee, "E_A")
    subject = seeded.get(Employee, "E_B")
    decision = authorize_qa_subject(seeded, asker, subject)
    assert decision.allowed is True
    assert decision.subject_type == "peer"


def test_cross_department_denied(seeded: Session) -> None:
    """跨部门拒绝 —— 即使汇报线同级。"""
    asker = seeded.get(Employee, "E_A")
    subject = seeded.get(Employee, "E_X")
    decision = authorize_qa_subject(seeded, asker, subject)
    assert decision.allowed is False
    assert decision.audit_level == "high"


def test_manager_denied(seeded: Session) -> None:
    """★ 上一级拒绝。"""
    asker = seeded.get(Employee, "E_A")
    subject = seeded.get(Employee, "M_SALES")
    decision = authorize_qa_subject(seeded, asker, subject)
    assert decision.allowed is False


def test_different_reporting_line_denied(seeded: Session) -> None:
    """同部门但汇报线不同级 ⇒ 拒绝。"""
    asker = seeded.get(Employee, "E_A")
    subject = seeded.get(Employee, "E_C")
    decision = authorize_qa_subject(seeded, asker, subject)
    assert decision.allowed is False


def test_direct_report_allowed(seeded: Session) -> None:
    """直属下属放行。"""
    asker = seeded.get(Employee, "M_SALES")
    subject = seeded.get(Employee, "E_A")
    decision = authorize_qa_subject(seeded, asker, subject)
    assert decision.allowed is True
    assert decision.subject_type == "report"


def test_indirect_report_allowed(seeded: Session) -> None:
    """★ 汇报线闭包：下属的下属放行。"""
    asker = seeded.get(Employee, "M_SALES")
    subject = seeded.get(Employee, "E_C")
    decision = authorize_qa_subject(seeded, asker, subject)
    assert decision.allowed is True
    assert decision.subject_type == "report"


def test_hr_allowed_anywhere(seeded: Session) -> None:
    """HR 跨部门放行。"""
    asker = seeded.get(Employee, "E_HR")
    subject = seeded.get(Employee, "E_X")
    decision = authorize_qa_subject(seeded, asker, subject)
    assert decision.allowed is True
    assert decision.subject_type == "hr"


def test_denied_message_does_not_leak_level_or_department(seeded: Session) -> None:
    """★ 拒绝文案不得区分「跨部门」与「上一级」。

    区分会让用户通过文案差异推断目标人的层级/部门。
    """
    asker = seeded.get(Employee, "E_A")
    cross_dept = authorize_qa_subject(seeded, asker, seeded.get(Employee, "E_X"))
    manager = authorize_qa_subject(seeded, asker, seeded.get(Employee, "M_SALES"))
    assert cross_dept.user_message == manager.user_message
    assert cross_dept.guidance == manager.guidance
    # 文案里不能出现具体的关系词
    assert "跨部门" not in cross_dept.user_message
    assert "上级" not in cross_dept.user_message


# ---------------------------------------------------------------------------
# 写操作拦截
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "question",
    [
        "帮我把分数改成95分",
        "把张三的分数调高一点",
        "修改一下我的绩效权重",
        "帮我录个实测值",
        "确认评级吧",
        "把分数设为100",
    ],
)
def test_write_action_detected(question: str) -> None:
    """写操作意图必须被识别。"""
    assert detect_write_action(question) is not None


@pytest.mark.parametrize(
    "question",
    [
        "我这个月绩效多少分",
        "为什么被扣了分",
        "哪些指标没参与",
        "部门的绩效分布怎么样",
        "帮我看看我的考核记录",
        # ⚠️ 对抗用例：这些**看起来**像写操作，实际是查询 ——
        #    误伤会直接毁掉问答体验。
        "我被扣了多少分",
        "分数是怎么算出来的",
        "我的权重是多少",
        "指标权重怎么分配的",
        "规则是什么来着",
        "评级标准是什么",
        "实测值填了吗",
        "我的考核记录在哪看",
        "帮我确认一下我的自评填了没",
        "上个月我改过自评，绩效多少分",
    ],
)
def test_read_questions_not_flagged(question: str) -> None:
    """正常查询**不能**被误判为写操作 —— 误伤会毁掉体验。

    ⚠️ 「我被扣了多少分」含「扣」与「分」；
    ⚠️ 「分数是怎么算出来的」含「分」与「算」；
    ⚠️ 「我的权重是多少」含「权重」但没有写动词；
    ⚠️ 「规则是什么来着」含「规则」但没有写动词；
    ⚠️ 「实测值填了吗」含「实测值」与「填」——
       ⚠️ 但「填了吗」是**询问状态**不是**填写动作**。
    """
    assert detect_write_action(question) is None


@pytest.mark.parametrize(
    "question",
    [
        # ⚠️ **疑问句式的写操作** —— 最容易漏的一类。
        #    疑问词检查若先行，这些会被当成"询问"而放过。
        "能不能把分数改成95分",
        "可以帮我调高一下分数吗",
        "帮我确认下评级行不行",
        "我想改分，该怎么改",
        "帮忙把权重改成40行吗",
    ],
)
def test_question_form_write_actions_still_intercepted(question: str) -> None:
    """★ 疑问句式**不能**成为绕过写操作拦截的后门。

    ⚠️ 这是关键词法的固有弱点 —— 所以本模块的写操作拦截
    只是**快速路径**，真正的安全边界在「工具层不提供写工具」。
    本测试保证快速路径本身没有明显漏洞。
    """
    assert detect_write_action(question) is not None


# ---------------------------------------------------------------------------
# resolve_scope
# ---------------------------------------------------------------------------


def _make_task(db: Session, period: str = "202611") -> str:
    task = AssessmentTask(
        id="T1",
        name="2026 年 11 月月度考核",
        period=period,
        department_id="D_SALES",
        status="published",
    )
    db.add(task)
    db.flush()
    return task.id


def test_resolve_scope_self_unchanged(seeded: Session) -> None:
    """⚠️ 不传 subject_id ⇒ 行为与改造前完全一致（查自己）。"""
    task_id = _make_task(seeded)
    seeded.add(AssessmentRecord(id="R_A", task_id=task_id, employee_id="E_A"))
    seeded.commit()

    scope = resolve_scope(seeded, seeded.get(Employee, "E_A"), task_id, None)
    assert scope.subject_id == "E_A"
    assert scope.subject_type == "self"
    assert set(scope.allowed_tools) == {
        "get_metric_values", "get_evidence", "get_scoring_breakdown",
    }


def test_resolve_scope_peer_gets_cropped_tool(seeded: Session) -> None:
    """★ 平级同事 ⇒ 只给裁剪版工具（不含证据/评语）。"""
    task_id = _make_task(seeded)
    seeded.add(AssessmentRecord(id="R_A", task_id=task_id, employee_id="E_A"))
    seeded.add(AssessmentRecord(id="R_B", task_id=task_id, employee_id="E_B"))
    seeded.commit()

    scope = resolve_scope(
        seeded, seeded.get(Employee, "E_A"), task_id, None, subject_id="E_B"
    )
    assert scope.subject_type == "peer"
    assert scope.subject_id == "E_B"
    # ⚠️ 关键断言：平级拿不到证据原文与完整评分工具
    assert scope.allowed_tools == ("get_peer_score",)
    assert "get_evidence" not in scope.allowed_tools


def test_resolve_scope_report_gets_full_tools(seeded: Session) -> None:
    """下属 ⇒ 完整只读工具。"""
    task_id = _make_task(seeded)
    seeded.add(AssessmentRecord(id="R_A", task_id=task_id, employee_id="E_A"))
    seeded.commit()

    scope = resolve_scope(
        seeded, seeded.get(Employee, "M_SALES"), task_id, None, subject_id="E_A"
    )
    assert scope.subject_type == "report"
    assert "get_evidence" in scope.allowed_tools
    assert "get_peer_score" not in scope.allowed_tools


def test_resolve_scope_cross_department_raises_403(seeded: Session) -> None:
    """跨部门 ⇒ 403。"""
    from fastapi import HTTPException

    task_id = _make_task(seeded)
    seeded.add(AssessmentRecord(id="R_X", task_id=task_id, employee_id="E_X"))
    seeded.commit()

    with pytest.raises(HTTPException) as exc:
        resolve_scope(
            seeded, seeded.get(Employee, "E_A"), task_id, None, subject_id="E_X"
        )
    assert exc.value.status_code == 403


def test_resolve_scope_manager_raises_403(seeded: Session) -> None:
    """★ 上一级 ⇒ 403。"""
    from fastapi import HTTPException

    task_id = _make_task(seeded)
    seeded.add(AssessmentRecord(id="R_M", task_id=task_id, employee_id="M_SALES"))
    seeded.commit()

    with pytest.raises(HTTPException) as exc:
        resolve_scope(
            seeded, seeded.get(Employee, "E_A"), task_id, None, subject_id="M_SALES"
        )
    assert exc.value.status_code == 403


def test_resolve_scope_unknown_subject_raises_404(seeded: Session) -> None:
    """目标人不存在 ⇒ 404。"""
    from fastapi import HTTPException

    task_id = _make_task(seeded)
    with pytest.raises(HTTPException) as exc:
        resolve_scope(
            seeded, seeded.get(Employee, "E_A"), task_id, None, subject_id="NOBODY"
        )
    assert exc.value.status_code == 404


def test_joint_appointment_peer_allowed(seeded: Session) -> None:
    """兼岗员工在兼岗部门可与该部门同级别同事互查。"""
    task_id = _make_task(seeded)
    seeded.add(AssessmentRecord(id="R_A", task_id=task_id, employee_id="E_A"))
    seeded.add(AssessmentRecord(id="R_X", task_id=task_id, employee_id="E_X"))
    seeded.commit()

    # E_X 兼岗到 D_SALES，汇报线改成 M_SALES（与 E_A 同级）
    e_x = seeded.get(Employee, "E_X")
    e_x.manager_id = "M_SALES"
    seeded.add(
        EmployeeDepartment(
            employee_id="E_X", department_id="D_SALES", is_primary=False
        )
    )
    seeded.commit()

    scope = resolve_scope(
        seeded, seeded.get(Employee, "E_A"), task_id, None, subject_id="E_X"
    )
    assert scope.subject_type == "peer"
