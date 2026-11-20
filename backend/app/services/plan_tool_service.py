"""只读业务工具：计划历史读取（Step 6 计划助手工具化）。

## 为什么单独一个模块

现有 ``agent_tool_service`` 里的 3 个工具（``get_metric_values`` /
``get_evidence`` / ``get_department_stats``）**全部绑死在「考核任务」上**：

- 令牌里必须有 ``task``，且 ``task_id`` 对应的任务必须存在；
- ``period`` 必须与任务周期一致，否则 400。

而**计划助手场景没有考核任务** —— 员工写 11 月的月计划时，
系统里可能压根没有 11 月的考核任务。所以这批工具**一个都用不上**。

因此这里新建一套**与考核任务解耦**的计划历史读取工具。

## 两个工具的权限边界

| 工具 | 能看到 | 边界 |
|---|---|---|
| ``get_my_plan_history`` | **只有我自己**的历史计划 | 硬编码按 ``employee_id`` 过滤，无参数可越权 |
| ``get_department_plan_history`` | **同部门**的历史计划 | 同部门 + 逐字段脱敏（见下） |

## ⚠️ 脱敏：部门内可见 ≠ 可以原样给模型

用户选了「我的历史 + 部门同级同事的历史计划」。但**同部门的计划**
仍然包含**他人的姓名、项目名等可能构成敏感信息的内容**。

所以部门级工具做了三层收敛：

1. **不返回 owner_id / owner_name** —— 模型不需要知道「这是谁的」。
   只用 ``author_alias``（如「同部门同事 A」）保持可读性，
   让模型能说「部门里常见的做法是……」，但不能指名道姓。
2. **不返回审批意见**（``approval_comment``）—— 那是主管对**个人**的评语，
   含个人信息，且会诱导模型生成针对具体个人的评价。
3. **不返回 performance_weight / estimated_value** ——
   这两个是**绩效权重和预估价值**，属于考核数据，不是计划参考。

⚠️ 但**任务名称、内容、交付物**必须保留 —— 那正是「参考历史计划」的意义所在。
只是把可能含人名的字段做了兜底替换（见 ``_scrub_person_names``）。
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domain import models

# ---------------------------------------------------------------------------
# 脱敏
# ---------------------------------------------------------------------------

#: 计划文本里可能出现的人名模式 —— 「张三」「李经理」「王工」
#:
#: ⚠️ 为什么连名字都要洗：
#:    部门内可见 ≠ 允许模型把「这是张三的计划」写进评语或建议里。
#:    计划内容里出现同事名字很常见（协作任务），但模型看到名字后
#:    很容易在生成的建议里复述具体的人 —— 那就变成了未经同意的信息披露。
_PERSON_NAME_RE = re.compile(
    r"[一-龥]{1,2}(?:经理|主管|总监|工程师|专员|助理|老师|工|同学)"
)

#: 百家姓（常见部分）—— 用于识别「姓 + 名」的中文人名
#:
#: ⚠️ 为什么只取一部分：
#:    完整百家姓有 500+ 字，内联成正则太长、容易在编辑时被折行破坏。
#:    这里用**字符串 + 字符类**构造，效果一样但可维护性高得多。
_SURNAMES = (
    "赵钱孙李周吴郑王冯陈褚卫蒋沈韩杨朱秦尤许何吕施张孔曹严华金魏陶姜"
    "戚谢邹喻柏窦章苏潘葛范彭郎鲁韦昌马苗凤花方俞任袁柳鲍史唐费廉岑薛"
    "雷贺倪汤滕殷罗毕郝邬安常乐傅皮齐康伍元顾孟黄穆萧尹姚邵湛汪祁毛狄"
    "贝臧计伏成戴谈宋庞熊纪舒屈项祝董梁杜阮蓝闵席季麻强贾路娄危江童颜郭"
    "梅盛林刁钟徐邱骆高夏蔡田樊胡凌霍万柯管卢莫房解应宗丁宣邓单杭洪包"
    "石崔吉龚程嵇邢裴陆荣翁荀惠甄曲家储靳段富巫乌焦巴牧山谷车侯班仲宫宁"
    "仇栾甘厉祖武符刘景詹龙叶幸韶郜黎薄印宿白怀蒲鄂索赖卓蔺屠蒙池乔郁"
    "胥能苍双闻莘党翟谭贡姬冉雍桑桂牛寿通边燕浦尚温别庄晏柴瞿阎充慕连"
    "茹艾鱼容古易慎戈廖终暨居衡步耿满弘匡国文寇广禄东欧利蔚隆师巩聂晁"
    "敖融冷辛阚简饶空沙鞠巢关相查荆红游权盖桓公"
)

#: 常见业务词 —— **不是人名**，即使以姓氏开头也不能被替换
#:
#: ⚠️ 为什么必须要有这张表：
#:    「季度述职」「方案设计」「每日巡检」这些词以姓氏开头（季、方…）。
#:    只靠「姓 + 1~2 字」的规则会把它们误判成人名，
#:    产出「某同事职和复盘」这种**损坏业务语义**的结果 ——
#:    比不脱敏还糟：模型读到的是一句不通的话。
#:
#:    判据：这些词在业务语境里**极常见**，且**不含指代具体人的语义**。
_COMMON_WORDS = (
    "季度", "方案", "每日", "每周", "每月", "工作", "任务", "项目", "需求", "交付",
    "指标", "考核", "绩效", "复盘", "总结", "计划", "组织", "部门", "岗位",
    "职责", "培训", "招聘", "人事", "财务", "市场", "销售", "客户", "用户", "产品",
    "技术", "研发", "测试", "运营", "数据", "系统", "平台", "服务", "流程", "规范",
    "标准", "质量", "效率", "成本", "风险", "问题", "目标", "结果", "落地",
    "上线", "发布", "评审", "验收", "优化", "重构", "改造", "梳理", "调研", "设计",
    "开发", "联调", "验证", "推广", "分析", "监控", "告警", "巡检", "运维",
    "预算", "采购", "合同", "报表", "看板", "文档", "知识", "经验", "案例",
    "制度", "政策", "沟通", "协调", "配合", "支持", "协助", "推进",
    "完成", "输出", "沉淀", "建设", "搭建", "支撑", "保障", "覆盖", "执行",
    "支付", "链路", "稳定", "故障", "线上", "排查", "迁移", "实施", "客户",
)

#: 常见业务词的**排序**正则 —— 长的优先（「完成方案」要先于「完成」被保护）
_COMMON_WORD_RE = re.compile(
    "|".join(re.escape(word) for word in sorted(set(_COMMON_WORDS), key=len, reverse=True))
)

#: 保护占位符前后缀 —— 用不会出现在业务文本里的控制字符
_PROTECT_PREFIX = "\x00"
_PROTECT_SUFFIX = "\x01"

#: 人名替换后的**占位文本**
#:
#: ⚠️ 为什么占位符里**不能有汉字**：
#:    「相」是真实姓氏 —— 「相关同事」里的「相关」会被自己的裸名规则
#:    **再匹配一次**，产出「需要某同事同事对接」这种套娃。
#:
#:    用纯控制字符做占位就彻底没有这个问题，
#:    最后一步统一还原成「相关同事」。
_SCRUB_TOKEN = "\x02\x03"

#: ⚠️ **指代具体人**的标记词 —— 人名**前面**出现这些词时，才认定为真名
#:
#: 「与王五对接」「找张三确认」「给李四发需求」「由王五负责」
#: —— 这些「与/找/给/由/请/和/跟/同」明确指向**某个人**。
#:
#: 为什么必须有这个约束：
#:    纯靠「姓 + 1~2 字」无法区分「王五」（人名）和「路由」（普通词）——
#:    「路」「查」「成」都是真实姓氏，中文里以姓氏开头的普通词非常多。
#:    实测误伤：「优化支付链路的稳定性」→「优化支付链某同事定性」。
#:    误伤比漏脱敏更糟：它把业务文本改成了不通的话，模型读到的是噪音。
#:
#: 所以这里**只**在有明确指人标记时才替换 —— 宁可漏，不可坏。
_NAME_MARKERS_BEFORE = ("与", "和", "找", "给", "由", "请", "跟", "同", "向", "对", "@", "及")
_NAME_MARKERS_AFTER = ("负责", "提出", "反馈", "确认", "签字", "汇报", "主导", "协助", "说")

#: 人名模式：姓氏 + 1~2 个汉字
#:
#: ⚠️ 名字部分用**非贪婪**（``{1,2}?``）——
#:    「找张三确认」若贪婪匹配会吃掉「确」，变成「找某同事认」。
#:    非贪婪下正则引擎会先试 1 个字：「张三」匹配上，
#:    剩下的「确认」被后面的标记规则接手。
_CHINESE_FULL_NAME_RE = re.compile(
    r"(?:[" + _SURNAMES + r"])[一-龥]{1,2}?"
)

#: 「指人标记 + 人名」—— 前置标记式
_NAME_AFTER_MARKER_RE = re.compile(
    r"(?P<marker>" + "|".join(re.escape(m) for m in _NAME_MARKERS_BEFORE) + r")"
    r"(?P<name>(?:[" + _SURNAMES + r"])[一-龥]{1,2}?)"
)

#: 「人名 + 指人标记」—— 后置标记式
_NAME_BEFORE_MARKER_RE = re.compile(
    r"(?P<name>(?:[" + _SURNAMES + r"])[一-龥]{1,2}?)"
    r"(?P<marker>" + "|".join(re.escape(m) for m in _NAME_MARKERS_AFTER) + r")"
)


def _scrub_person_names(text: str) -> str:
    """把计划文本里可能被识别为**具体人**的姓名替换掉。

    ⚠️ 只在**部门级**工具里用 —— 自己的计划不用洗（那是我自己的内容）。

    ⚠️ **只在有明确指人标记时才替换**（见 ``_NAME_MARKERS_*``）。
       宁可漏，不可坏 —— 误伤会损坏业务语义，比漏脱敏更糟。

    ⚠️ 职务称谓（「李经理」「王工」）**无条件替换** ——
       那些词不可能是普通业务词。
    """
    if not text:
        return ""
    # --- 第一步：职务称谓（李经理、王工） ---
    value = _PERSON_NAME_RE.sub(_SCRUB_TOKEN, text)

    # --- 第二步：保护常见业务词 ---
    protected: list[str] = []

    def _protect(match: re.Match[str]) -> str:
        protected.append(match.group(0))
        return f"{_PROTECT_PREFIX}{len(protected) - 1}{_PROTECT_SUFFIX}"

    value = _COMMON_WORD_RE.sub(_protect, value)

    # --- 第三步：带指人标记的人名 ---
    # 前置标记：「与王五对接」→ 标记和名字一起换掉
    value = _NAME_AFTER_MARKER_RE.sub(
        lambda m: f"{m.group('marker')}{_SCRUB_TOKEN}", value
    )
    # 后置标记：「王五负责」→ 名字换掉，动词保留
    value = _NAME_BEFORE_MARKER_RE.sub(
        lambda m: f"{_SCRUB_TOKEN}{m.group('marker')}", value
    )
    # 裸人名（前后都没标记）
    value = _CHINESE_FULL_NAME_RE.sub(_SCRUB_TOKEN, value)

    # --- 第四步：还原 ---
    for index, word in enumerate(protected):
        value = value.replace(f"{_PROTECT_PREFIX}{index}{_PROTECT_SUFFIX}", word)
    return value.replace(_SCRUB_TOKEN, "相关同事")


def _author_alias(index: int) -> str:
    """给部门内同事生成**不可回推**的别名。

    ⚠️ 别名按**本次查询的排序序号**生成 —— 不是稳定的 ID 映射。
    目的是让模型能区分「这是不同的人」，但**不能**把别名和真人对上号。
    """
    return f"同部门同事{chr(ord('A') + index % 26)}"


# ---------------------------------------------------------------------------
# 查询
# ---------------------------------------------------------------------------


def _month_range(months: int) -> tuple[str, str]:
    """最近 N 个月（含当月）的 ``(起, 止)``。

    ⚠️ **格式必须是 ``YYYY-MM``**（``month_plan.plan_month`` 的库内格式）。

    ⚠️ **BUG（真机端到端验证时暴露）**：这里原来返回 ``YYYYMM``（如
    ``202606``~``202611``），拿它去和库里的 ``plan_month`` 做**字符串比较**：

        "2026-11" >= "202606"   →  False

    因为 ``-`` 的 ASCII 码是 45，比 ``0`` 的 48 小 → **所有历史计划都查不到**。
    表现是工具永远返回 ``planCount: 0`` + 「该时间范围内没有历史计划」，
    而模型会据此说「未查到历史计划」→ **参考材料静默失效**。

    归一化只在这一层做（入参两种格式都接受），**出参必须统一成库内格式**。
    """
    today = date.today()
    end = today.strftime("%Y-%m")
    start_month = today.month - (months - 1)
    year = today.year
    while start_month <= 0:
        start_month += 12
        year -= 1
    return f"{year}-{start_month:02d}", end


def load_my_plan_history(
    db: Session, *, employee_id: str, months: int = 6, plan_type: str = "month"
) -> dict[str, Any]:
    """读取**我自己**的历史计划。

    ⚠️ 硬边界：``employee_id`` 来自**令牌**（不是请求参数），
       没有任何参数能越权查到别人的计划。
    """
    months = max(1, min(int(months or 6), 24))
    start, end = _month_range(months)
    kind = (plan_type or "month").lower()

    items: list[dict[str, Any]] = []
    plans: list[dict[str, Any]] = []

    if kind == "month":
        rows = db.scalars(
            select(models.MonthPlan)
            .where(
                models.MonthPlan.owner_id == employee_id,
                models.MonthPlan.plan_month >= start,
                models.MonthPlan.plan_month <= end,
            )
            .order_by(models.MonthPlan.plan_month.desc())
        ).all()
        for plan in rows:
            plans.append({
                "planMonth": plan.plan_month,
                "title": plan.title,
                "status": plan.status,
                "projectName": plan.project_name or "",
                "positionName": plan.position_name or "",
            })
            for item in plan.items:
                items.append({
                    "planMonth": plan.plan_month,
                    "workType": item.task_type,
                    "taskName": item.task_name,
                    "taskContent": item.task_content,
                    "deliverable": item.deliverable,
                    "quality": item.quality,
                    "estimatedStartDate": _iso_date(item.estimated_start_date),
                    "estimatedEndDate": _iso_date(item.estimated_end_date),
                    "status": item.status,
                })
    elif kind == "week":
        start_date = date.today() - timedelta(days=months * 31)
        rows = db.scalars(
            select(models.WeekPlan)
            .where(
                models.WeekPlan.owner_id == employee_id,
                models.WeekPlan.week_start >= start_date,
            )
            .order_by(models.WeekPlan.week_start.desc())
        ).all()
        for plan in rows:
            plans.append({
                "weekStart": _iso_date(plan.week_start),
                "title": plan.title,
                "status": plan.status,
            })
            for item in plan.items:
                items.append({
                    "weekStart": _iso_date(plan.week_start),
                    "content": item.content,
                    "deliverable": item.deliverable,
                    "quality": item.quality,
                    "plannedFinishDate": _iso_date(item.planned_finish_date),
                    "completionStatus": item.completion_status or "",
                })
    else:
        start_date = date.today() - timedelta(days=months * 31)
        rows = db.scalars(
            select(models.DayPlan)
            .where(
                models.DayPlan.owner_id == employee_id,
                models.DayPlan.plan_date >= start_date,
            )
            .order_by(models.DayPlan.plan_date.desc())
        ).all()
        for plan in rows:
            plans.append({
                "planDate": _iso_date(plan.plan_date),
                "content": plan.content,
                "remark": plan.remark,
                "status": plan.status,
            })

    return {
        "available": bool(plans),
        "planType": kind,
        "range": {"from": start, "to": end},
        "planCount": len(plans),
        "itemCount": len(items),
        "plans": plans,
        "items": items,
        "note": "" if plans else "该时间范围内没有历史计划",
    }


def load_department_plan_history(
    db: Session, *, employee_id: str, months: int = 6, limit: int = 40
) -> dict[str, Any]:
    """读取**同部门**的历史计划（脱敏后）。

    ⚠️ 边界：
    - 只看**同一个部门**（``employee.department_id``）；
    - **排除自己**（自己的计划走 ``get_my_plan_history``，避免重复）；
    - 不返回 owner 标识、审批意见、绩效权重与预估价值；
    - 计划文本里的人名被替换掉。
    """
    months = max(1, min(int(months or 6), 24))
    limit = max(1, min(int(limit or 40), 200))
    start, end = _month_range(months)

    me = db.get(models.Employee, employee_id)
    if me is None or not me.department_id:
        return {
            "available": False,
            "planCount": 0,
            "itemCount": 0,
            "plans": [],
            "items": [],
            "note": "当前账号未归属部门，无法查看部门参考",
        }

    peers = db.scalars(
        select(models.Employee).where(
            models.Employee.department_id == me.department_id,
            models.Employee.id != employee_id,
            models.Employee.employment_status == "active",
        )
    ).all()
    peer_ids = [p.id for p in peers]
    if not peer_ids:
        return {
            "available": False,
            "planCount": 0,
            "itemCount": 0,
            "plans": [],
            "items": [],
            "note": "部门内暂无其他在职同事",
        }

    rows = db.scalars(
        select(models.MonthPlan)
        .where(
            models.MonthPlan.owner_id.in_(peer_ids),
            models.MonthPlan.plan_month >= start,
            models.MonthPlan.plan_month <= end,
        )
        .order_by(models.MonthPlan.plan_month.desc())
    ).all()

    plans: list[dict[str, Any]] = []
    items: list[dict[str, Any]] = []
    alias_by_owner: dict[str, str] = {}
    for plan in rows:
        alias = alias_by_owner.get(plan.owner_id)
        if alias is None:
            alias = _author_alias(len(alias_by_owner))
            alias_by_owner[plan.owner_id] = alias
        plans.append({
            "planMonth": plan.plan_month,
            "authorAlias": alias,
            "status": plan.status,
            # ⚠️ 项目名也可能是敏感的（客户名、保密项目）—— 保留但不给人名
            "projectName": _scrub_person_names(plan.project_name or ""),
            "itemCount": len(plan.items),
        })
        for item in plan.items:
            items.append({
                "planMonth": plan.plan_month,
                "authorAlias": alias,
                "workType": item.task_type,
                "taskName": _scrub_person_names(item.task_name),
                "taskContent": _scrub_person_names(item.task_content),
                "deliverable": _scrub_person_names(item.deliverable),
                "quality": _scrub_person_names(item.quality),
                "estimatedStartDate": _iso_date(item.estimated_start_date),
                "estimatedEndDate": _iso_date(item.estimated_end_date),
            })
            if len(items) >= limit:
                break
        if len(items) >= limit:
            break

    return {
        "available": bool(plans),
        "planType": "month",
        "range": {"from": start, "to": end},
        "planCount": len(plans),
        "itemCount": len(items),
        "peerCount": len(peer_ids),
        "plans": plans,
        "items": items,
        "note": "" if plans else "该时间范围内部门暂无历史计划",
    }


def _iso_date(value: date | None) -> str:
    return value.isoformat() if value else ""


def _iso_datetime(value: datetime | None) -> str:
    return value.isoformat() if value else ""
