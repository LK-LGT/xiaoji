"""P0-8：飞书端的**同事名候选匹配**。

⚠️ **这个模块解决什么问题**
--------------------------
用户在飞书里问「销售乙这个月多少分」——
机器人得知道「销售乙」指的是谁。

⚠️ **核心安全设计：候选集先按权限收窄，再匹配名字**
---------------------------------------------------
这是整个 P0-8 最关键的一点：

    ==========================  ==========================================
    做法                        用户体验
    ==========================  ==========================================
    ❌ 先匹配全公司名字，        「找到了张三，但你不无权查看」
       再判权限
    ✅ 先按同部门+同级别圈定       「没找到员工『张三』」
       候选集，再匹配名字
    ==========================  ==========================================

⚠️ **为什么必须后者**
------------------
前者会**泄露信息**：
  - 「找到了但无权查看」⇒ 泄露**此人存在**
  - 「你无权查看该同事」⇒ 泄露**此人的层级与部门**

后者在越权目标上表现得**和不存在一样** ——
用户无法从响应推断出「张三是否在这个公司」。

⇒ 这与 ``qa_authorization`` 的拒绝文案统一（「不在可见范围内」）是**同一条原则**。

⚠️ **匹配口径**
--------------
- **精确匹配优先**（姓名完全相同）
- **包含匹配兜底**（问题里出现姓名，如「销售乙这个月多少分」）
- ⚠️ **姓氏匹配要谨慎** —— 「张」会匹配到「张三」「张小明」「张伟」，
  误匹配率太高 ⇒ 只在**候选集很小**且**无歧义**时才用。

⚠️ **本模块不做的事**
--------------------
- **不**做 LLM 实体抽取（P1 才做）—— 规则匹配够用且可预测
- **不**决定最终能不能查 —— 那是 ``qa_authorization.authorize_qa_subject`` 的事
  本模块只负责「**候选集里有没有这个人**」
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from ..domain.models import Employee, EmployeeDepartment
from . import access_control

#: ⚠️ 姓名**精确匹配** —— 用户直接说全名（「查一下张三」）
_EXACT_NAME = "exact"

#: ⚠️ 姓名**包含匹配** —— 名字嵌在句子里（「销售乙这个月多少分」）
_CONTAINS_NAME = "contains"

#: ⚠️ 姓氏**前缀匹配** —— 只在候选集唯一时使用（「查一下老张」）
_PREFIX_NAME = "prefix"


@dataclass
class SubjectCandidate:
    """一个候选同事。"""

    employee_id: str
    name: str
    #: 命中的匹配方式（exact / contains / prefix）
    match_type: str = ""
    #: 所在部门名（用于卡片上区分同名同事）
    department_name: str = ""
    #: 岗位（飞书 job_title 自由文本，仅用于展示）
    position: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "employee_id": self.employee_id,
            "name": self.name,
            "match_type": self.match_type,
            "department_name": self.department_name,
            "position": self.position,
            "label": self.label(),
        }

    def label(self) -> str:
        """卡片上显示的标签 —— 同名同事必须能区分。

        ⚠️ **部门 + 岗位仍然可能相同**（同名同岗同部门完全合法）——
           那种情况由 ``_assign_unique_labels`` 追加序号兜底。
        """
        parts = [self.name]
        if self.department_name:
            parts.append(self.department_name)
        if self.position:
            parts.append(self.position)
        return " · ".join(parts)


def _assign_unique_labels(candidates: list[SubjectCandidate]) -> None:
    """⚠️ **保证每个候选的标签唯一** —— 原地修改。

    ⚠️ **为什么必须保证唯一**
    ----------------------
    同名 + 同部门 + 同岗位是**完全合法**的情况（如两个"李四 · 销售部 · 销售代表"）。
    卡片上出现两个一模一样的条目 ⇒ 用户**选不出来**
    ⇒ 等于功能不可用。

    ⚠️ **追加序号是最诚实的做法** ——
       不编造"看起来有信息"的东西（那可能是错的），
       只保证**可区分**：用户点错了顶多换个序号再试。

    ⚠️ **不泄露越权信息** ——
       序号只在**已收窄的候选集内部**使用，
       候选集里的人都是有权查的 ⇒ 不泄露任何东西。
    """
    counts: dict[str, int] = {}
    for candidate in candidates:
        counts[candidate.label()] = counts.get(candidate.label(), 0) + 1
    if all(count == 1 for count in counts.values()):
        return

    # ⚠️ 有重复 ⇒ 全部重复项都追加序号（不能只给其中一个加，
    #    否则两个标签还是一样）。
    seen: dict[str, int] = {}
    for candidate in candidates:
        base = candidate.label()
        if counts.get(base, 0) <= 1:
            continue
        seen[base] = seen.get(base, 0) + 1
        candidate.position = (
            f"{candidate.position}（{seen[base]}）" if candidate.position
            else f"（{seen[base]}）"
        )


@dataclass
class SubjectMatchResult:
    """匹配结果。

    ⚠️ ``candidates`` 的长度决定后续行为：
    - 0 个 ⇒ 没匹配到（按本人处理，或提示没找到）
    - 1 个 ⇒ 直接用
    - ≥2 个 ⇒ **让用户选**（返回选择卡片）
    """

    #: 命中的候选
    candidates: list[SubjectCandidate] = field(default_factory=list)
    #: 候选集里有多少人（用于区分「没匹配到」与「匹配到但歧义」）
    pool_size: int = 0

    @property
    def matched(self) -> bool:
        return bool(self.candidates)

    @property
    def ambiguous(self) -> bool:
        """⚠️ 多个候选 ⇒ 需要用户明确选。"""
        return len(self.candidates) > 1

    @property
    def single(self) -> SubjectCandidate | None:
        return self.candidates[0] if len(self.candidates) == 1 else None


#: ⚠️ **代词** —— 「他」「她」「他们」「大家」这类不指名道姓的说法。
#:
#: ⚠️ 这些**不参与名字匹配** —— 否则「他这个月多少分」会被误判成
#:    「有个叫『他』的同事」。
#:
#: ⚠️ 「他」这类词**可以**作为「继承上一轮主体」的信号 ——
#:    但那是 P0-5 会话上下文负责的，不在本模块。
PRONOUNS: frozenset[str] = frozenset({
    "他", "她", "它", "他们", "她们", "大家", "同事", "同部门的人",
    "其他人", "大家伙",
})

#: ⚠️ **匹配用的停用词** —— 这些词太常见，会造成大量误匹配。
#:
#: ⚠️ 「同事」尤其重要 —— 用户说「同事这个月多少分」时，
#:    问的是**所有同事**，不是某个叫「同事」的人。
_COMMON_WORDS: frozenset[str] = frozenset({
    "同事", "他", "她", "他们", "大家", "我", "我们", "自己",
    "本月", "上月", "这个月", "上个月", "今天", "昨天", "明天",
    "绩效", "考核", "分数", "得分", "成绩", "评分", "评级",
    "部门", "公司", "团队", "组里",
})


def _is_meaningful_name(name: str) -> bool:
    """这个字符串**适合**当作人名去匹配吗？"""
    stripped = name.strip()
    if len(stripped) < 2:
        # ⚠️ 单字太容易误匹配（「王」会命中所有姓王的）
        return False
    if stripped in PRONOUNS:
        return False
    if stripped in _COMMON_WORDS:
        return False
    return True


#: ⚠️ **「老/小/阿」前缀** —— 中文口语里用它们代替名字（「老张」「小李」）。
_FAMILIAR_PREFIXES: tuple[str, ...] = ("老", "小", "阿")


def _surname_prefix(question: str) -> str:
    """从问题里提取「姓氏前缀」—— 供前缀匹配使用。

    ⚠️ **只认两种形态**（都很窄，宁可漏匹配也不要误匹配）：

    ① 整个问题**就是一个姓氏**（「李」「张」）——
       ⇒ 取整个问题。
    ② 口语称呼「老张」「小李」「阿三」——
       ⇒ 去掉「老/小/阿」后取首字。

    ⚠️ **绝不能**拿整个句子的首字去比 ——
       「张三这个月多少分」的首字是"张"，
       那样会让**所有**以张开头的同事都被拉进来。
    """
    text = question.strip()
    if not text:
        return ""
    # ① 整个问题就是一个字 ⇒ 那就是姓氏
    if len(text) == 1:
        return text
    # ② 口语称呼
    for prefix in _FAMILIAR_PREFIXES:
        if text.startswith(prefix) and len(text) >= 2:
            return text[1]
    return ""


def peer_candidate_pool(
    db: Session,
    asker: Employee,
    *,
    limit: int = 200,
) -> list[Employee]:
    """**同部门 + 同级别**的同事池（提问者可互查的范围）。

    ⚠️ **这是权限收窄的核心** ——
       候选集只包含 ``is_peer_viewable`` 会放行的人。
       ⇒ 跨部门 / 跨级 / 上级的员工**根本不在池子里**。

    ⚠️ **在职过滤** —— 离职员工的绩效不通过问答开放。

    ⚠️ **为什么用「同部门集合 ∩ 同级」在 SQL 里粗筛，再逐个精确判定**：
       汇报线同级的口径是「同一直属上级」，
       同部门的口径是「部门集合有交集」（兼岗）——
       这两个条件用 SQL 表达都很绕。
       ⇒ 先用**便宜的 SQL 粗筛**（同部门 ∩ 同 manager）缩小到几十人，
       再逐个跑 ``is_peer_viewable`` 做**精确判定**。
       ⇒ 判定逻辑只有一处（``is_peer_viewable``），不会出现"两套口径"。

    Args:
        db: 数据库会话。
        asker: 提问者。
        limit: 候选池上限（防止超大部门拖慢）。

    Returns:
        可互查的同事列表（不含提问者自己）。
    """
    if asker is None:
        return []

    context = access_control.build_access_context(db, asker)

    # ── ① SQL 粗筛：同部门 ∩ 同直属上级 ───────────────────────
    asker_departments = access_control.employee_department_ids(db, asker)
    if not asker_departments:
        return []

    stmt = (
        select(Employee)
        .outerjoin(
            EmployeeDepartment,
            EmployeeDepartment.employee_id == Employee.id,
        )
        .where(
            # ⚠️ **主部门也要算** —— `employee_department_ids` 的口径是
            #    「主部门 ∪ 兼岗部门」。只 join 兼岗表会漏掉
            #    「只有主部门、没建 EmployeeDepartment 行」的员工
            #    ⇒ 候选池会**空掉**，功能直接不可用。
            or_(
                Employee.department_id.in_(asker_departments),
                EmployeeDepartment.department_id.in_(asker_departments),
            ),
            Employee.id != asker.id,
            Employee.employment_status == "active",
            Employee.account_status == "enabled",
        )
        .distinct()
    )
    if asker.manager_id:
        # ⚠️ 有上级 ⇒ 只看同一直属上级的人（同级）
        stmt = stmt.where(Employee.manager_id == asker.manager_id)
    else:
        # ⚠️ 没有上级（部门根节点）⇒ 同级判定是「双方都没有上级」
        stmt = stmt.where(Employee.manager_id.is_(None))

    rows = list(db.scalars(stmt).all())
    if len(rows) > limit:
        rows = rows[:limit]

    # ── ② 精确判定：逐个跑 is_peer_viewable ───────────────────
    # ⚠️ 这里**不复刻**判定逻辑 —— 复用唯一的实现，
    #    避免"粗筛口径"与"精确口径"漂移。
    allowed = [row for row in rows if access_control.is_peer_viewable(db, context, row)]
    return allowed


def _department_names(db: Session, employee_ids: list[str]) -> dict[str, str]:
    """取候选人的主部门名（用于卡片区分同名）。

    ⚠️ **优先用 ``EmployeeDepartment.is_primary``**；
       没有兼岗记录时**回落到 ``Employee.department_id``** ——
       否则只建了主部门、没建 EmployeeDepartment 行的员工
       会没有部门名，卡片上的同名同事就区分不出来。
    """
    if not employee_ids:
        return {}
    from ..domain.models import Department

    rows = db.execute(
        select(Employee.id, Department.name)
        .join(EmployeeDepartment, EmployeeDepartment.employee_id == Employee.id)
        .join(Department, Department.id == EmployeeDepartment.department_id)
        .where(
            Employee.id.in_(employee_ids),
            EmployeeDepartment.is_primary.is_(True),
        )
    ).all()
    names = {emp_id: name for emp_id, name in rows}

    # ⚠️ 补齐没有「主部门标记行」的人。
    missing = [emp_id for emp_id in employee_ids if emp_id not in names]
    if missing:
        fallback = db.execute(
            select(Employee.id, Department.name)
            .join(Department, Department.id == Employee.department_id)
            .where(Employee.id.in_(missing))
        ).all()
        names.update({emp_id: name for emp_id, name in fallback})
    return names


def match_subject(
    db: Session,
    asker: Employee,
    question: str,
    *,
    pool: list[Employee] | None = None,
) -> SubjectMatchResult:
    """从问题里匹配同事名字。

    ⚠️ **候选集已经按权限收窄** —— 越权目标不在池子里，
       所以「没匹配到」和「无权查看」在用户看来**完全一样**。

    ⚠️ **匹配顺序**：精确 > 包含 > 前缀。
       精确匹配优先是���「张三」和「张三丰」同时存在时，
       说「张三」应该命中「张三」而不是「张三丰」。

    Args:
        db: 数据库会话。
        asker: 提问者。
        question: 用户原话。
        pool: 候选池（可传入以复用，避免重复查询）。

    Returns:
        ``SubjectMatchResult``。
    """
    if not question:
        return SubjectMatchResult()

    candidates = pool if pool is not None else peer_candidate_pool(db, asker)
    if not candidates:
        return SubjectMatchResult(pool_size=0)

    dept_names = _department_names(db, [c.id for c in candidates])
    matched: list[SubjectCandidate] = []

    # ── ① 精确匹配 ────────────────────────────────────────────
    text = question.strip()
    exact = [c for c in candidates if c.name == text]
    if exact:
        matched = [_to_candidate(c, _EXACT_NAME, dept_names) for c in exact]

    # ── ② 包含匹配 ────────────────────────────────────────────
    # ⚠️ **按姓名长度降序** —— 先匹配「张三丰」再匹配「张三」，
    #    否则问「张三丰多少分」会命中「张三」。
    if not matched:
        contains: list[Employee] = []
        for c in sorted(candidates, key=lambda x: -len(x.name)):
            if not _is_meaningful_name(c.name):
                continue
            if c.name in question and c not in contains:
                contains.append(c)
        if contains:
            matched = [_to_candidate(c, _CONTAINS_NAME, dept_names) for c in contains]

    # ── ③ 前缀匹配（姓氏）──────────────────────────────────────
    # ⚠️ **只在无歧义时使用** —— 「查老张」时如果池子里有
    #    「张三」「张小明」「张伟」三个姓张的，返回全部反而是噪声。
    #    ⇒ 只有恰好命中 1 人才返回。
    #
    # ⚠️ **只认「明确的姓氏前缀」** —— 用户说「查一下老张」时，
    #    能识别出"老张"里的"张"。
    #    ⇒ 不能拿整个句子去比首字（「张」会误伤任何以张开头的句子）。
    if not matched:
        surname = _surname_prefix(question)
        prefix = [
            c for c in candidates
            if surname and _is_meaningful_name(c.name) and c.name.startswith(surname)
        ]
        if len(prefix) == 1:
            matched = [_to_candidate(prefix[0], _PREFIX_NAME, dept_names)]

    # ⚠️ **统一出口** —— 标签去重在这里做一次，
    #    保证所有匹配路径的卡片都能区分同名同事。
    _assign_unique_labels(matched)
    return SubjectMatchResult(candidates=matched, pool_size=len(candidates))


def _to_candidate(
    employee: Employee, match_type: str, dept_names: dict[str, str]
) -> SubjectCandidate:
    return SubjectCandidate(
        employee_id=employee.id,
        name=employee.name,
        match_type=match_type,
        department_name=dept_names.get(employee.id, ""),
        position=employee.position or "",
    )


def extract_names_from_text(text: str) -> list[str]:
    """从文本里粗略提取「可能的人名」。

    ⚠️ **本函数只用于调试与展示**（如"你可能想问：张三"），
       真正的匹配靠 ``match_subject``（限定在权限收窄后的候选集内）。

    ⚠️ 用中文书名号/引号包裹的名字是最可靠的信号：
    「查一下『张三』」—— 引号里的几乎一定是人名。
    """
    if not text:
        return []
    found: list[str] = []
    # ⚠️ 引号内的内容
    for pattern in (r"「([^」]{2,20})」", r"『([^』]{2,20})』", r'"([^"]{2,20})"'):
        for m in re.finditer(pattern, text):
            name = m.group(1).strip()
            if _is_meaningful_name(name):
                found.append(name)
    return found


__all__ = [
    "PRONOUNS",
    "SubjectCandidate",
    "SubjectMatchResult",
    "extract_names_from_text",
    "match_subject",
    "peer_candidate_pool",
]
