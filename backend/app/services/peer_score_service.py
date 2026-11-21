"""P0-4b：同部门平级同事的分数查询（**只读 + 字段裁剪**）。

⚠️ **为什么单独一个模块，而不是复用 ``get_scoring_breakdown``**
--------------------------------------------------------------
1. **字段裁剪必须是"写死"的**，不能靠调用点分支 ——
   复用现有工具意味着要在每个返回点加"是不是 peer"的判断，
   **漏一个分支就泄露一次**。
   独立模块的返回字段是**白名单式**的：不在白名单里的字段，
   无论怎么改都不会出去。

2. **口径不同**：本工具回答的是「**同部门同事**完成得怎么样」，
   不是「**我**的分数是怎么算出来的」。

3. **契约稳定**：改本模块不影响现有工具的返回结构。

⚠️ **字段白名单（本模块的核心安全资产）**
------------------------------------------
✅ **给**：总分、等级、各指标达成率与贡献分、算式、未参与指标及原因
❌ **不给**：
   - 主管评语正文（``manager_comment``）—— 人身评价，横向传播风险高
   - 证据原文 / 摘要（``Evidence.summary``）—— 可能含客户资料、内部文档
   - 自评内容（``self_review_content``）—— 员工私人陈述
   - 申诉 / 复核记录 —— 涉个人争议
   - 联系方式、工号、邮箱 —— PII 最小化
   - 等级完整区间表（``level_scale``）—— 属内部算分配置
   - 其他周期的分数 —— 画像级数据

⚠️ **裁剪在服务端完成，不依赖模型"别说"**
------------------------------------------
模型拿不到的数据，模型就说不出来。
这是"不把安全边界交给模型"在**字段层面**的落地 ——
与权限层（谁能查）是两道独立的闸门。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domain import models

#: ✅ peer 查询允许返回的指标字段（白名单）。
#:
#: ⚠️ **只列白名单，不列黑名单** —— 落库结构新增字段时，
#:    白名单不会自动放行，必须显式添加才能出去。
#:    这与"黑名单"相反但更安全：新增字段默认**不可见**。
PEER_METRIC_FIELDS: tuple[str, ...] = (
    "metric_code",
    "metric_name",
    "value",
    "target",
    "unit",
    "ratio",
    "level",
    "level_desc",
    "raw_weight",
    "normalized_weight",
    "level_score",
    "contribution",
)


def load_peer_score(
    db: Session,
    *,
    employee_id: str,
    task: models.AssessmentTask,
) -> dict[str, Any]:
    """同部门平级同事的绩效摘要（**只读 + 裁剪**）。

    ⚠️ **调用方必须已完成授权判定**（``qa_authorization.authorize_qa_subject``）。
       本函数**不做**权限检查 —— 权限在上一层（工具网关）做，
       这样权限逻辑只有一处，不会出现"某个调用点忘了检查"。

    ⚠️ **只给当前周期** —— 不给历史轨迹。
       12 个周期的分数 + 等级序列属于**画像级**数据，
       横向传播的风险远大于"完成情况"的实际收益。
    """
    record = db.execute(
        select(models.AssessmentRecord).where(
            models.AssessmentRecord.task_id == task.id,
            models.AssessmentRecord.employee_id == employee_id,
        )
    ).scalars().first()
    if record is None:
        return {
            "available": False,
            "metrics": [],
            "note": "该同事在本考核周期还没有绩效记录",
        }

    score = db.execute(
        select(models.Score).where(models.Score.record_id == record.id)
    ).scalars().first()
    if score is None:
        return {
            "available": False,
            "metrics": [],
            "note": "该同事的绩效尚未评分完成，暂时无法查看",
        }

    trace = score.trace_json or {}
    metrics: list[dict[str, Any]] = []
    for entry in score.per_metric_json or []:
        # ⚠️ 白名单裁剪 —— 不在白名单的字段一律不出。
        item = {key: entry.get(key) for key in PEER_METRIC_FIELDS if key in entry}
        # 算式 —— 让 AI 能直接说「权重 30% × 等级分 80 = 贡献 24 分」
        normalized = item.get("normalized_weight")
        level_score = item.get("level_score")
        contribution = item.get("contribution")
        if normalized is not None and level_score is not None and contribution is not None:
            item["formula"] = f"{normalized} × {level_score} = {contribution}"
        metrics.append(item)

    # ── 等级 ───────────────────────────────────────────────
    # ⚠️ 只给**最终确认等级**（confirmed_rating）与规则等级（trace 里的）。
    #    等级区间表（level_scale）不给 —— 那是内部算分配置。
    confirmed_rating = record.confirmed_rating
    rule_rating = trace.get("scheme_rating") or trace.get("rating")

    # ── 未参与算分的指标：只给「原因」，不给指标明细 ─────────
    # ⚠️ 这里**不**返回 excluded_items 的完整明细 ——
    #    未参与的原因对本级同事是有用信息（「他那个指标没数据」），
    #    但原始的排除项列表可能带出目标值等内部信息。
    #    ⇒ 只给聚合后的原因说明。
    excluded_reason = _excluded_reason(trace, score)

    return {
        "available": True,
        "period": task.period,
        "total_score": score.total,
        "confirmed_rating": confirmed_rating,
        "rule_rating": rule_rating,
        "metrics": metrics,
        "excluded_reason": excluded_reason,
        "participating_count": trace.get("participating_count"),
        "participating_weight": trace.get("participating_weight"),
        "note": (
            "以上为该同事本考核周期的绩效摘要（仅分数与指标构成）。"
            "评语、证据与自评内容不在同事互查范围内。"
        ),
    }


def _excluded_reason(trace: dict[str, Any], score: models.Score) -> str | None:
    """未参与算分的指标的**原因说明**（不含明细）。

    ⚠️ 为什么要给原因：用户问「他怎么少了一档」时，
       答案是「某指标没数据所以没参与算分，权重被重新归一」。
       不说清楚会让人误以为"缺数据 = 扣分"。
    """
    warnings = trace.get("warnings") or []
    if not warnings:
        return None
    # ⚠️ 只保留**分类**信息，不回传原始 warning 文本 ——
    #    原始文本可能含指标名 + 目标值等内部细节。
    categories: list[str] = []
    for warning in warnings:
        if not isinstance(warning, str):
            continue
        # ⚠️ 匹配要覆盖多种措辞 ——
        #   「缺目标值」/「无目标值」/「缺少目标」/「target」
        #   「无数据」/「缺数据」/「缺少实测」
        #   「rubric」/「等级」
        lowered = warning.lower()
        if "目标" in warning and ("缺" in warning or "无" in warning or "未" in warning) or "target" in lowered:
            categories.append("缺少目标值")
        elif ("数据" in warning and ("缺" in warning or "无" in warning or "未" in warning)) or "no data" in lowered:
            categories.append("缺少实测数据")
        elif "rubric" in lowered or "等级" in warning:
            categories.append("等级配置缺失")
        else:
            categories.append("其他规则原因")
    # 去重且保序
    unique = list(dict.fromkeys(categories))
    if not unique:
        return None
    return "有指标未参与算分（" + "、".join(unique) + "），其余指标权重已重新归一"
