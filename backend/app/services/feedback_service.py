"""P2-3 —— 案例库 + A/B 灰度回流 Prompt。

三块能力
--------
1. **反馈回流**（``record_feedback``）：人工对 AI 草稿的采纳/修改/拒绝，
   写入 ``AiFeedback``。这是灰度对比的**唯一可信依据** ——
   模型自评不算数。
2. **Prompt 灰度**（``resolve_prompt``）：按 ``PromptVersion.rollout``
   决定用哪个版本。命中是**确定性**的：同一 ``record_id`` 永远落同一个桶，
   否则同一条记录两次生成用不同 prompt，灰度对比失去意义。
3. **案例库**（``publish_case`` / ``search_cases``）：把已定稿的结论沉淀成
   可参考的案例，**强制脱敏**，不存员工身份与评语正文。

灰度对比（``compare_rollout``）
------------------------------
只对比**人工反馈**，不对比模型自评。样本量不足时明确返回「样本不足」，
不硬下结论 —— 5 条反馈和 500 条反馈的结论强度完全不同。
"""

from __future__ import annotations

import hashlib
import logging
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..domain.models import (
    AiFeedback,
    AssessmentRecord,
    CaseLibraryEntry,
    Employee,
    PromptVersion,
    Score,
)

logger = logging.getLogger(__name__)

#: 反馈动作枚举
FEEDBACK_ACTIONS = ("ACCEPT", "MODIFY", "REJECT")

#: 灰度模式的最小样本量 —— 低于此值不下结论
MIN_SAMPLE_FOR_CONCLUSION = 30

#: 灰度模式：off / canary / full
ROLLOUT_MODES = ("off", "canary", "full")


class FeedbackError(Exception):
    """反馈 / 案例库操作失败。"""

    status_code = 400

    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


# ---------------------------------------------------------------------------
# 1. 反馈回流
# ---------------------------------------------------------------------------


def record_feedback(
    db: Session,
    *,
    record_id: str,
    actor_id: str,
    action: str,
    agent: str = "comment_draft",
    prompt_version: str | None = None,
    diff_summary: str | None = None,
    reason: str | None = None,
    dimension: str | None = None,
) -> AiFeedback:
    """记录一条人工反馈。

    ⚠️ 反馈**只落 ``ai_feedback`` 表**，不触碰 Score / CommentDraft / 状态机。
    反馈是「对 AI 产出的评价」，不是「对绩效结论的修改」——
    后者走 ``ScoreAdjustment`` 流程。
    """
    if action not in FEEDBACK_ACTIONS:
        raise FeedbackError(
            f"反馈动作非法：{action}，必须是 {' / '.join(FEEDBACK_ACTIONS)}"
        )
    rec = db.get(AssessmentRecord, record_id)
    if rec is None:
        raise FeedbackError("考核记录不存在", 404)
    actor = db.get(Employee, actor_id)
    if actor is None:
        raise FeedbackError("反馈人不存在", 404)

    row = AiFeedback(
        record_id=record_id,
        agent=agent,
        prompt_version=prompt_version,
        action=action,
        actor_id=actor_id,
        diff_summary=(diff_summary or "").strip() or None,
        reason=(reason or "").strip() or None,
        dimension=dimension,
    )
    db.add(row)
    db.flush()
    return row


def serialize_feedback(row: AiFeedback) -> dict[str, Any]:
    return {
        "id": row.id,
        "recordId": row.record_id,
        "agent": row.agent,
        "promptVersion": row.prompt_version,
        "action": row.action,
        "actorId": row.actor_id,
        "diffSummary": row.diff_summary,
        "reason": row.reason,
        "dimension": row.dimension,
        "createdAt": row.created_at.isoformat() if row.created_at else None,
    }


# ---------------------------------------------------------------------------
# 2. Prompt 灰度
# ---------------------------------------------------------------------------


def _bucket(record_id: str, agent: str, version: str) -> int:
    """确定性分桶：返回 0~9999 的桶号。

    用 ``record_id + agent + version`` 的哈希 —— **不掺随机数**，
    保证同一输入永远落同一个桶。
    """
    digest = hashlib.sha256(f"{record_id}|{agent}|{version}".encode()).hexdigest()
    return int(digest[:8], 16) % 10000


def upsert_prompt_version(
    db: Session,
    *,
    agent: str,
    version: str,
    prompt_text: str,
    rollout: str = "off",
    canary_ratio: float = 0.0,
    baseline_version: str | None = None,
    note: str | None = None,
    created_by: str | None = None,
) -> PromptVersion:
    """新增或更新一个 Prompt 版本。"""
    if rollout not in ROLLOUT_MODES:
        raise FeedbackError(f"灰度模式非法：{rollout}，必须是 {' / '.join(ROLLOUT_MODES)}")
    if not 0.0 <= canary_ratio <= 1.0:
        raise FeedbackError("灰度比例必须在 0~1 之间")
    if rollout == "canary" and canary_ratio <= 0:
        raise FeedbackError("灰度模式下比例必须大于 0")
    if not agent.strip() or not version.strip():
        raise FeedbackError("agent 与 version 不能为空")

    row = db.scalar(
        select(PromptVersion).where(
            PromptVersion.agent == agent, PromptVersion.version == version
        )
    )
    if row is None:
        row = PromptVersion(agent=agent, version=version, prompt_text=prompt_text)
        db.add(row)
    row.prompt_text = prompt_text
    row.rollout = rollout
    row.canary_ratio = canary_ratio
    row.baseline_version = baseline_version
    row.note = note
    row.created_by = created_by
    db.flush()
    return row


def resolve_prompt(
    db: Session, *, agent: str, record_id: str
) -> dict[str, Any]:
    """决定这条记录该用哪个 Prompt 版本。

    规则（**确定性**）：
    - ``full`` 的版本优先（最新创建的那个）；
    - 否则找 ``canary`` 版本，按 ``_bucket`` 判定是否命中；
    - 都没命中 → 回落到 ``baseline_version`` 指定的版本；
    - 再没有 → ``None``（调用方走默认模板）。

    ⚠️ **不放大范围**：灰度只能影响「命中桶内」的记录，
    绝不能让某个版本意外接管全部流量。
    """
    versions = db.scalars(
        select(PromptVersion)
        .where(PromptVersion.agent == agent)
        .order_by(PromptVersion.created_at.desc())
    ).all()

    full = [v for v in versions if v.rollout == "full"]
    if full:
        chosen = full[0]
        return {
            "version": chosen.version,
            "promptText": chosen.prompt_text,
            "bucket": None,
            "mode": "full",
            "baselineVersion": chosen.baseline_version,
        }

    canaries = [v for v in versions if v.rollout == "canary"]
    for candidate in canaries:
        bucket = _bucket(record_id, agent, candidate.version)
        if bucket < int(candidate.canary_ratio * 10000):
            return {
                "version": candidate.version,
                "promptText": candidate.prompt_text,
                "bucket": bucket,
                "mode": "canary",
                "baselineVersion": candidate.baseline_version,
            }

    # 未命中任何灰度桶 → 回落基线。
    # 注意：基线回落到的是「被引用为 baseline 的那个版本」，
    # 引用方自身的 rollout 状态不限（off 的版本同样可以作基线）。
    for candidate in versions:
        if not candidate.baseline_version:
            continue
        baseline = db.scalar(
            select(PromptVersion).where(
                PromptVersion.agent == agent,
                PromptVersion.version == candidate.baseline_version,
            )
        )
        if baseline is not None:
            return {
                "version": baseline.version,
                "promptText": baseline.prompt_text,
                "bucket": None,
                "mode": "baseline",
                "baselineVersion": baseline.version,
            }

    return {
        "version": None,
        "promptText": None,
        "bucket": None,
        "mode": "none",
        "baselineVersion": None,
    }


def serialize_prompt_version(row: PromptVersion) -> dict[str, Any]:
    return {
        "id": row.id,
        "agent": row.agent,
        "version": row.version,
        "promptText": row.prompt_text,
        "rollout": row.rollout,
        "canaryRatio": row.canary_ratio,
        "baselineVersion": row.baseline_version,
        "note": row.note,
        "createdBy": row.created_by,
        "createdAt": row.created_at.isoformat() if row.created_at else None,
    }


# ---------------------------------------------------------------------------
# 3. 灰度对比
# ---------------------------------------------------------------------------


def compare_rollout(
    db: Session, *, agent: str, versions: list[str] | None = None
) -> dict[str, Any]:
    """对比不同 Prompt 版本的**人工反馈**表现。

    只看人工反馈（ACCEPT / MODIFY / REJECT），不看模型自评。
    样本不足时明确返回「样本不足」—— 5 条反馈推不出可靠结论。
    """
    stmt = select(AiFeedback).where(AiFeedback.agent == agent)
    if versions:
        stmt = stmt.where(AiFeedback.prompt_version.in_(versions))
    rows = db.scalars(stmt).all()

    buckets: dict[str, dict[str, Any]] = {}
    for row in rows:
        key = row.prompt_version or "(none)"
        bucket = buckets.setdefault(
            key, {"version": key, "total": 0, "ACCEPT": 0, "MODIFY": 0, "REJECT": 0}
        )
        bucket["total"] += 1
        if row.action in bucket:
            bucket[row.action] += 1

    results = []
    for bucket in buckets.values():
        total = bucket["total"]
        accept = bucket["ACCEPT"]
        modify = bucket["MODIFY"]
        reject = bucket["REJECT"]
        # 采纳率：完全采纳 + 部分采纳（修改后采纳）加权
        # 「修改后采纳」算 0.5 —— 它既不是成功也不是失败。
        accept_rate = round((accept + modify * 0.5) / total, 4) if total else None
        results.append(
            {
                **bucket,
                "acceptRate": accept_rate,
                "rejectRate": round(reject / total, 4) if total else None,
            }
        )

    results.sort(key=lambda r: (-(r["acceptRate"] or 0), r["version"]))
    total_samples = sum(r["total"] for r in results)
    enough = total_samples >= MIN_SAMPLE_FOR_CONCLUSION

    winner = results[0]["version"] if enough and results else None
    return {
        "agent": agent,
        "sampleSize": total_samples,
        "minSampleForConclusion": MIN_SAMPLE_FOR_CONCLUSION,
        "sufficient": enough,
        "versions": results,
        "recommendedVersion": winner,
        "conclusion": (
            f"样本 {total_samples} 条（≥{MIN_SAMPLE_FOR_CONCLUSION}），"
            f"建议版本 {winner}。"
            if enough
            else f"样本仅 {total_samples} 条（<{MIN_SAMPLE_FOR_CONCLUSION}），"
            "样本不足以下结论，仅供观察。"
        ),
        "note": "对比依据为人工反馈，非模型自评。",
    }


# ---------------------------------------------------------------------------
# 4. 案例库
# ---------------------------------------------------------------------------


def publish_case(
    db: Session,
    *,
    title: str,
    scenario: str,
    resolution: str,
    actor_id: str,
    source_record_id: str | None = None,
    score_range: str | None = None,
) -> CaseLibraryEntry:
    """把一条已定稿的结论沉淀成案例（**强制脱敏**）。

    入库门槛：
    1. 若给了 ``source_record_id``，该记录必须**已确认评级**
       （未确认的结论不进案例库 —— 案例库是「已定稿做法」的参考）；
    2. 事实摘要只取**数值与结构化字段**，**不含姓名、不含评语正文**；
    3. ``redacted`` 强制为 True。
    """
    if not title.strip():
        raise FeedbackError("案例标题不能为空")
    if not resolution.strip():
        raise FeedbackError("处理要点不能为空")

    facts: dict[str, Any] = {}
    rating: str | None = None
    if source_record_id:
        rec = db.get(AssessmentRecord, source_record_id)
        if rec is None:
            raise FeedbackError("来源考核记录不存在", 404)
        if not rec.confirmed_rating:
            raise FeedbackError(
                "只能收录已确认评级的结论 —— 未定稿的结论不进案例库"
            )
        rating = rec.confirmed_rating
        score = db.scalar(select(Score).where(Score.record_id == source_record_id))
        if score is not None:
            facts["total"] = score.total
            # 只取指标名与达成率 —— 不取任何能定位到人的字段
            facts["metrics"] = [
                {
                    "metric": pm.get("metric_name") or pm.get("metric_code"),
                    "ratio": pm.get("ratio"),
                    "level": pm.get("level"),
                }
                for pm in (score.per_metric_json or [])
            ]

    row = CaseLibraryEntry(
        title=title.strip(),
        scenario=scenario.strip() or "general",
        rating=rating,
        score_range=score_range,
        facts_json=facts,
        resolution=resolution.strip(),
        source_record_id=source_record_id,
        redacted=True,  # 强制：案例库永不存未脱敏内容
        created_by=actor_id,
    )
    db.add(row)
    db.flush()
    return row


def search_cases(
    db: Session,
    *,
    scenario: str | None = None,
    keyword: str | None = None,
    limit: int = 20,
) -> list[CaseLibraryEntry]:
    """检索案例（按场景 + 关键词）。"""
    stmt = select(CaseLibraryEntry)
    if scenario:
        stmt = stmt.where(CaseLibraryEntry.scenario == scenario)
    if keyword:
        like = f"%{keyword.strip()}%"
        stmt = stmt.where(
            CaseLibraryEntry.title.like(like) | CaseLibraryEntry.resolution.like(like)
        )
    return list(
        db.scalars(
            stmt.order_by(CaseLibraryEntry.use_count.desc(), CaseLibraryEntry.created_at.desc())
            .limit(limit)
        ).all()
    )


def mark_case_used(db: Session, *, case_id: str) -> None:
    """标记案例被使用（用于热度排序）。"""
    row = db.get(CaseLibraryEntry, case_id)
    if row is not None:
        row.use_count = (row.use_count or 0) + 1
        db.flush()


def serialize_case(row: CaseLibraryEntry) -> dict[str, Any]:
    return {
        "id": row.id,
        "title": row.title,
        "scenario": row.scenario,
        "rating": row.rating,
        "scoreRange": row.score_range,
        "facts": row.facts_json or {},
        "resolution": row.resolution,
        "sourceRecordId": row.source_record_id,
        "redacted": row.redacted,
        "useCount": row.use_count,
        "createdBy": row.created_by,
        "createdAt": row.created_at.isoformat() if row.created_at else None,
    }


# ---------------------------------------------------------------------------
# 汇总
# ---------------------------------------------------------------------------


def feedback_stats(db: Session, *, agent: str) -> dict[str, Any]:
    """反馈总量统计（用于运营看板）。"""
    rows = db.execute(
        select(AiFeedback.action, func.count())
        .where(AiFeedback.agent == agent)
        .group_by(AiFeedback.action)
    ).all()
    counts = {action: count for action, count in rows}
    total = sum(counts.values())
    return {
        "agent": agent,
        "total": total,
        "accept": counts.get("ACCEPT", 0),
        "modify": counts.get("MODIFY", 0),
        "reject": counts.get("REJECT", 0),
        "acceptRate": (
            round((counts.get("ACCEPT", 0) + counts.get("MODIFY", 0) * 0.5) / total, 4)
            if total
            else None
        ),
    }
