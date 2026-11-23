"""P0-8：飞书端同事名候选匹配 + 多候选选择卡片测试。

⚠️ **这个文件锁定的是三条核心契约**
--------------------------------------
1. **候选集先收窄，再匹配名字** —— 越权的人**根本不在候选集里**。
   ⇒ 「没找到」与「无权查看」在用户看来**完全一样**，
   不泄露此人是否存在、属于哪个部门、什么层级。

2. **多候选不猜** —— 匹配到多个同事时，机器人**必须**让用户选，
   绝不自己挑一个。猜错就是答错人的绩效。

3. **单候选直接用 / 零候选回落本人** ——
   单个候选 ⇒ 直接查他；零候选 ⇒ 当作查自己（既有行为不变）。

⚠️ **为什么这三条要单独一个文件**
----------------------------------
这是 P0 里**唯一**在飞书端新增「指名道姓查人」能力的改动。
名字匹配一旦写错，要么答错人（数据错误），
要么泄露越权信息（安全事故）。
⇒ 契约必须**独立、完整、可逐条核对**。
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
from backend.app.services import feishu_card, qa_subject_matcher

# ---------------------------------------------------------------------------
# 夹具
# ---------------------------------------------------------------------------


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


def _add_employee(
    db: Session,
    employee_id: str,
    *,
    name: str,
    department_id: str | None,
    manager_id: str | None = None,
    position: str = "",
    employment_status: str = "active",
    account_status: str = "enabled",
    extra_departments: tuple[str, ...] = (),
) -> Employee:
    employee = Employee(
        id=employee_id,
        name=name,
        department_id=department_id,
        manager_id=manager_id,
        position=position,
        employment_status=employment_status,
        account_status=account_status,
    )
    db.add(employee)
    for dept_id in extra_departments:
        db.add(
            EmployeeDepartment(
                employee_id=employee_id,
                department_id=dept_id,
                is_primary=False,
            )
        )
    db.flush()
    return employee


def _seed_org(db: Session) -> None:
    """建组织，覆盖全部匹配场景。

    销售部（D_SALES）：
        M_SALES   销售经理（→ D_ROOT_BOSS）        ← 提问者的**上级**
        E_SALES1  张三（→ M_SALES）                ← **提问者**
        E_SALES2  张三丰（→ M_SALES）              ← 同部门同级
        E_SALES3  李四（→ M_SALES）                ← 同部门同级
        E_SALES4  王五（→ M_SALES2）               ← 同部门但**跨级**
        E_OUT     张三丰（→ M_MKT）                ← **跨部门同名**

    市场部（D_MKT）：
        M_MKT     市场经理
        E_MKT1    赵六（→ M_MKT）
    """
    db.add_all(
        [
            Department(id="D_ROOT", name="总部", active=True),
            Department(id="D_SALES", name="销售部", active=True),
            Department(id="D_MKT", name="市场部", active=True),
            Department(id="D_HR", name="人力资源部", active=True),
        ]
    )
    db.flush()

    db.add_all(
        [
            _add_employee(
                db, "D_ROOT_BOSS", name="总裁",
                department_id="D_ROOT", manager_id=None,
            ),
            _add_employee(
                db, "M_SALES", name="销售经理",
                department_id="D_SALES", manager_id="D_ROOT_BOSS",
            ),
            _add_employee(
                db, "E_SALES1", name="张三",
                department_id="D_SALES", manager_id="M_SALES",
                position="销售代表",
            ),
            _add_employee(
                db, "E_SALES2", name="张三丰",
                department_id="D_SALES", manager_id="M_SALES",
                position="高级销售",
            ),
            _add_employee(
                db, "E_SALES3", name="李四",
                department_id="D_SALES", manager_id="M_SALES",
                position="销售代表",
            ),
            _add_employee(
                db, "E_SALES4", name="王五",
                department_id="D_SALES", manager_id="E_SALES2",
                position="销售代表",
            ),
            _add_employee(
                db, "E_OUT", name="张三丰",
                department_id="D_MKT", manager_id="M_MKT",
                position="市场代表",
            ),
            _add_employee(
                db, "M_MKT", name="市场经理",
                department_id="D_MKT", manager_id="D_ROOT_BOSS",
            ),
            _add_employee(
                db, "E_MKT1", name="赵六",
                department_id="D_MKT", manager_id="M_MKT",
            ),
            _add_employee(
                db, "E_HR", name="HR专员",
                department_id="D_HR", manager_id="D_ROOT_BOSS",
            ),
        ]
    )
    db.flush()
    db.add(HrDepartmentConfig(department_id="D_HR", active=True))
    db.commit()


def _emp(db: Session, employee_id: str) -> Employee:
    employee = db.get(Employee, employee_id)
    assert employee is not None
    return employee


def _seed_task(db: Session, *, task_id: str, period: str, employee_ids: tuple[str, ...]) -> None:
    """给若干员工在某周期建考核任务与记录。"""
    task = AssessmentTask(
        id=task_id,
        name=f"{period} 月度考核",
        period=period,
        status="completed",
        department_id="D_SALES",
    )
    db.add(task)
    db.flush()
    for emp_id in employee_ids:
        db.add(
            AssessmentRecord(
                id=f"AR_{task_id}_{emp_id}",
                task_id=task_id,
                employee_id=emp_id,
                status="submitted",
            )
        )
    db.flush()
    db.commit()


# ---------------------------------------------------------------------------
# ① 候选集收窄 —— 越权的人根本不在池子里
# ---------------------------------------------------------------------------


def test_pool_excludes_manager(db: Session) -> None:
    """⚠️ **上级不在候选池里** —— 需求原文「不能查上一级」。"""
    _seed_org(db)
    pool = qa_subject_matcher.peer_candidate_pool(db, _emp(db, "E_SALES1"))
    ids = {e.id for e in pool}
    assert "M_SALES" not in ids, "上级不能出现在可查同事池里"


def test_pool_excludes_cross_department(db: Session) -> None:
    """⚠️ **跨部门不在候选池里** —— 需求原文「不能跨部门查询」。"""
    _seed_org(db)
    pool = qa_subject_matcher.peer_candidate_pool(db, _emp(db, "E_SALES1"))
    ids = {e.id for e in pool}
    assert "E_MKT1" not in ids
    assert "E_OUT" not in ids


def test_pool_excludes_different_reporting_level(db: Session) -> None:
    """⚠️ **汇报线不同级不在候选池里**（同部门也不行）。"""
    _seed_org(db)
    pool = qa_subject_matcher.peer_candidate_pool(db, _emp(db, "E_SALES1"))
    ids = {e.id for e in pool}
    assert "E_SALES4" not in ids, "E_SALES4 的上级是 E_SALES2，与提问者不同级"


def test_pool_includes_same_department_same_level(db: Session) -> None:
    """同部门 + 同级 ⇒ 在候选池里。"""
    _seed_org(db)
    pool = qa_subject_matcher.peer_candidate_pool(db, _emp(db, "E_SALES1"))
    ids = {e.id for e in pool}
    assert "E_SALES2" in ids
    assert "E_SALES3" in ids


def test_pool_excludes_self(db: Session) -> None:
    """候选池里**不含自己** —— 查自己是另一条路径。"""
    _seed_org(db)
    pool = qa_subject_matcher.peer_candidate_pool(db, _emp(db, "E_SALES1"))
    assert "E_SALES1" not in {e.id for e in pool}


def test_pool_excludes_inactive(db: Session) -> None:
    """离职员工不在候选池里。"""
    _seed_org(db)
    _add_employee(
        db, "E_LEFT", name="离职同事",
        department_id="D_SALES", manager_id="M_SALES",
        employment_status="resigned",
    )
    db.commit()
    pool = qa_subject_matcher.peer_candidate_pool(db, _emp(db, "E_SALES1"))
    assert "E_LEFT" not in {e.id for e in pool}


def test_pool_excludes_disabled_account(db: Session) -> None:
    """账号停用的人不在候选池里。"""
    _seed_org(db)
    _add_employee(
        db, "E_DISABLED", name="停用同事",
        department_id="D_SALES", manager_id="M_SALES",
        account_status="disabled",
    )
    db.commit()
    pool = qa_subject_matcher.peer_candidate_pool(db, _emp(db, "E_SALES1"))
    assert "E_DISABLED" not in {e.id for e in pool}


def test_pool_is_symmetric(db: Session) -> None:
    """互查必须对称 —— A 能查 B，B 也能查 A。"""
    _seed_org(db)
    pool_a = qa_subject_matcher.peer_candidate_pool(db, _emp(db, "E_SALES1"))
    pool_b = qa_subject_matcher.peer_candidate_pool(db, _emp(db, "E_SALES2"))
    assert "E_SALES2" in {e.id for e in pool_a}
    assert "E_SALES1" in {e.id for e in pool_b}


def test_part_time_department_peer_is_included(db: Session) -> None:
    """⚠️ **兼岗同事也要能查** —— 部门口径是「集合有交集」。"""
    _seed_org(db)
    _add_employee(
        db, "E_PARTTIME", name="兼岗同事",
        department_id="D_MKT", manager_id="M_SALES",
        extra_departments=("D_SALES",),
    )
    db.commit()
    pool = qa_subject_matcher.peer_candidate_pool(db, _emp(db, "E_SALES1"))
    assert "E_PARTTIME" in {e.id for e in pool}


# ---------------------------------------------------------------------------
# ② 名字匹配
# ---------------------------------------------------------------------------


def test_exact_name_match(db: Session) -> None:
    """全名精确匹配。"""
    _seed_org(db)
    result = qa_subject_matcher.match_subject(db, _emp(db, "E_SALES1"), "李四")
    assert result.matched is True
    assert result.single is not None
    assert result.single.employee_id == "E_SALES3"
    assert result.single.match_type == qa_subject_matcher._EXACT_NAME


def test_contains_name_match(db: Session) -> None:
    """名字嵌在句子里 ⇒ 包含匹配。"""
    _seed_org(db)
    result = qa_subject_matcher.match_subject(
        db, _emp(db, "E_SALES1"), "李四这个月多少分",
    )
    assert result.single is not None
    assert result.single.employee_id == "E_SALES3"
    assert result.single.match_type == qa_subject_matcher._CONTAINS_NAME


def test_longer_name_wins(db: Session) -> None:
    """⚠️ **「张三丰」优先于「张三」** —— 问「张三丰」不能命中「张三」。

    这是包含匹配的**关键顺序规则**：
    若不按姓名长度降序，「张三丰多少分」会先命中「张三」。
    """
    _seed_org(db)
    result = qa_subject_matcher.match_subject(
        db, _emp(db, "E_SALES1"), "张三丰这个月多少分",
    )
    assert result.single is not None
    assert result.single.employee_id == "E_SALES2"


def test_cross_department_same_name_not_matched(db: Session) -> None:
    """⚠️ **跨部门同名同事不参与匹配** —— 且不会泄露。

    「张三丰」在销售部和市场部各有一个。
    销售部的提问者只能匹配到销售部那个 ——
    市场部那个**根本不在候选集里**，
    所以用户不会看到"有两个人但只有一个能选"这种泄露。
    """
    _seed_org(db)
    result = qa_subject_matcher.match_subject(
        db, _emp(db, "E_SALES1"), "张三丰这个月多少分",
    )
    assert result.single is not None
    assert result.single.employee_id == "E_SALES2"
    assert "E_OUT" not in {c.employee_id for c in result.candidates}


def test_manager_name_not_matched(db: Session) -> None:
    """⚠️ **上级不参与匹配** —— 问上级等于"没找到"。"""
    _seed_org(db)
    result = qa_subject_matcher.match_subject(
        db, _emp(db, "E_SALES1"), "销售经理这个月多少分",
    )
    assert result.matched is False


def test_no_name_in_text_returns_empty(db: Session) -> None:
    """句子里没人名 ⇒ 零候选（回落到查自己）。"""
    _seed_org(db)
    result = qa_subject_matcher.match_subject(
        db, _emp(db, "E_SALES1"), "我这个月多少分",
    )
    assert result.matched is False
    assert result.ambiguous is False


def test_pronoun_not_treated_as_name(db: Session) -> None:
    """⚠️ **代词不当作人名** —— 「他」不是某个同事的名字。"""
    _seed_org(db)
    result = qa_subject_matcher.match_subject(
        db, _emp(db, "E_SALES1"), "他这个月多少分",
    )
    assert result.matched is False


def test_common_word_not_treated_as_name(db: Session) -> None:
    """⚠️ **「同事」不当作人名** —— 问的是"所有同事"不是某个叫同事的人。"""
    _seed_org(db)
    _add_employee(
        db, "E_TONG", name="同事",
        department_id="D_SALES", manager_id="M_SALES",
    )
    db.commit()
    result = qa_subject_matcher.match_subject(
        db, _emp(db, "E_SALES1"), "同事这个月多少分",
    )
    assert result.matched is False


def test_surname_prefix_only_when_unambiguous(db: Session) -> None:
    """姓氏前缀匹配 —— **只在唯一命中**时使用。

    「老张」这种模糊说法，池子里有多个姓张的就**不匹配**
    （返回全部反而是噪声），让用户说得更具体。
    """
    _seed_org(db)
    # ⚠️ 提问者自己叫「张三」且**不在候选池里**（查自己走另一条路），
    #    所以池里姓张的只剩「张三丰」一个 ⇒ 「张」可以唯一匹配。
    result = qa_subject_matcher.match_subject(
        db, _emp(db, "E_SALES1"), "李",
    )
    # ⚠️ 「李」只有一个姓李的人 ⇒ 可以匹配。
    assert result.single is not None
    assert result.single.employee_id == "E_SALES3"
    assert result.single.match_type == qa_subject_matcher._PREFIX_NAME

    # ⚠️ 「张」只有一个（提问者自己不在池里）⇒ 仍然唯一。
    unambiguous = qa_subject_matcher.match_subject(
        db, _emp(db, "E_SALES1"), "张",
    )
    assert unambiguous.single is not None
    assert unambiguous.single.employee_id == "E_SALES2"

    # ⚠️ 再加一个姓张的 ⇒ 歧义 ⇒ **不做**前缀匹配。
    _add_employee(
        db, "E_ZHANG2", name="张小明",
        department_id="D_SALES", manager_id="M_SALES",
    )
    db.commit()
    ambiguous = qa_subject_matcher.match_subject(
        db, _emp(db, "E_SALES1"), "张",
    )
    assert ambiguous.matched is False, "多个同姓时不该用前缀匹配"


def test_ambiguous_when_multiple_candidates(db: Session) -> None:
    """⚠️ **多候选 ⇒ 标记为歧义** —— 交给用户选，绝不猜。"""
    _seed_org(db)
    # ⚠️ 造一个同名同级的同事。
    _add_employee(
        db, "E_DUP", name="李四",
        department_id="D_SALES", manager_id="M_SALES",
    )
    db.commit()
    result = qa_subject_matcher.match_subject(
        db, _emp(db, "E_SALES1"), "李四这个月多少分",
    )
    assert result.ambiguous is True
    assert len(result.candidates) == 2
    assert result.single is None


def test_candidate_label_distinguishes_same_name(db: Session) -> None:
    """⚠️ **同名同事必须能区分** —— 标签带部门与岗位。"""
    _seed_org(db)
    _add_employee(
        db, "E_DUP", name="李四",
        department_id="D_SALES", manager_id="M_SALES",
        position="销售代表",
    )
    db.commit()
    result = qa_subject_matcher.match_subject(
        db, _emp(db, "E_SALES1"), "李四",
    )
    labels = {c.label() for c in result.candidates}
    assert len(labels) == 2, "同名同事的标签必须不同，否则用户选不出来"


# ---------------------------------------------------------------------------
# ③ 选择卡片
# ---------------------------------------------------------------------------


def test_subject_picker_card_structure(db: Session) -> None:
    """选择卡的骨架要对 —— 层级错一个就是空白卡片。"""
    _seed_org(db)
    card = feishu_card.build_qa_subject_picker_card(
        candidates=[
            {"employee_id": "E_SALES2", "name": "张三丰", "label": "张三丰 · 销售部"},
            {"employee_id": "E_SALES3", "name": "李四", "label": "李四 · 销售部"},
        ],
        question="张三丰这个月多少分",
        period="202611",
    )
    assert card["schema"] == "2.0"
    assert card["header"]["title"]["tag"] == "plain_text"
    assert card["header"]["title"]["content"] == "选择查询对象"
    elements = card["body"]["elements"]
    assert elements[0]["text"]["tag"] == "plain_text"
    assert "张三丰 · 销售部" in elements[0]["text"]["content"]
    assert "1." in elements[0]["text"]["content"]
    assert "2." in elements[0]["text"]["content"]

    actions = [e for e in elements if e.get("tag") == "action"]
    assert len(actions) == 1
    buttons = actions[0]["actions"]
    assert len(buttons) == 2
    assert buttons[0]["value"]["action"] == feishu_card.ACTION_SELECT_SUBJECT
    assert buttons[0]["value"]["subject_id"] == "E_SALES2"
    assert buttons[0]["value"]["question"] == "张三丰这个月多少分"
    assert buttons[0]["value"]["period"] == "202611"
    assert buttons[1]["value"]["subject_id"] == "E_SALES3"


def test_subject_picker_card_uses_plain_text(db: Session) -> None:
    """⚠️ **人名用 plain_text** —— markdown 会把 ``<at>`` 解析成 @某人。"""
    _seed_org(db)
    card = feishu_card.build_qa_subject_picker_card(
        candidates=[{"employee_id": "E_X", "name": "<at id=1></at> 某人", "label": "x"}],
        question="q",
    )
    elements = card["body"]["elements"]
    assert elements[0]["text"]["tag"] == "plain_text"


def test_subject_picker_card_period_in_header(db: Session) -> None:
    """有周期时标题里要带上周期。"""
    _seed_org(db)
    card = feishu_card.build_qa_subject_picker_card(
        candidates=[{"employee_id": "E_X", "name": "某人", "label": "某人"}],
        question="q",
        period="202611",
    )
    assert "202611" in card["body"]["elements"][0]["text"]["content"]


def test_subject_picker_card_empty_candidates(db: Session) -> None:
    """零候选时不渲染按钮（但卡片本身要能渲染）。"""
    _seed_org(db)
    card = feishu_card.build_qa_subject_picker_card(candidates=[], question="q")
    elements = card["body"]["elements"]
    assert not [e for e in elements if e.get("tag") == "action"]


# ---------------------------------------------------------------------------
# ④ 端到端：匹配 → 卡片 → 回调
# ---------------------------------------------------------------------------


def test_end_to_end_single_match(db: Session) -> None:
    """单候选 ⇒ 直接用，不必弹卡。"""
    _seed_org(db)
    result = qa_subject_matcher.match_subject(
        db, _emp(db, "E_SALES1"), "李四这个月多少分",
    )
    assert result.single is not None
    assert result.single.employee_id == "E_SALES3"
    assert result.ambiguous is False


def test_end_to_end_multi_match_builds_card(db: Session) -> None:
    """多候选 ⇒ 生成选择卡，按钮带 subject_id。"""
    _seed_org(db)
    _add_employee(
        db, "E_DUP", name="李四",
        department_id="D_SALES", manager_id="M_SALES",
    )
    db.commit()
    result = qa_subject_matcher.match_subject(
        db, _emp(db, "E_SALES1"), "李四这个月多少分",
    )
    assert result.ambiguous is True
    card = feishu_card.build_qa_subject_picker_card(
        candidates=[c.to_dict() for c in result.candidates],
        question="李四这个月多少分",
        period="202611",
    )
    buttons = [
        b
        for e in card["body"]["elements"] if e.get("tag") == "action"
        for b in e["actions"]
    ]
    subject_ids = {b["value"]["subject_id"] for b in buttons}
    assert subject_ids == {"E_SALES3", "E_DUP"}


def test_subject_task_lookup_uses_subject_records(db: Session) -> None:
    """⚠️ **查同事时找的是同事名下的任务** —— 不是提问者自己的。

    这是 P0-8 最容易写错的地方：
    若按提问者找任务，就会拿提问者的 task_id 去查同事的数据，
    授权层会因「该同事在此任务无记录」而放行失败。
    """
    from backend.app.services import feishu_qa_ai

    _seed_org(db)
    _seed_task(db, task_id="T_MINE", period="202611", employee_ids=("E_SALES1",))
    _seed_task(db, task_id="T_PEER", period="202611", employee_ids=("E_SALES3",))

    mine = feishu_qa_ai.list_candidate_tasks(db, _emp(db, "E_SALES1"), "202611")
    assert {t.id for t in mine} == {"T_MINE"}

    peer = feishu_qa_ai.list_candidate_tasks(
        db, _emp(db, "E_SALES1"), "202611", subject_id="E_SALES3",
    )
    assert {t.id for t in peer} == {"T_PEER"}
