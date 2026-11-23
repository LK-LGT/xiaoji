"""网盘剪辑工作量 —— 复核工单服务（BP-2 / P2）。

流程
----
    员工对某天有异议
        ↓  列出那天系统的所有文件（含被排除的），员工勾选争议的
        ↓  系统没扫到的（比如漏传）→ 员工手填
        ↓  上传证据截图
        ↓  发起工单（快照当时判定值）
    主管裁定
        ↓  按天整体裁定（scope=day）：直接定这天最终多少条
        ↓  逐文件裁定（scope=file）：每个争议文件单独判
        ↓  结果写回 ``pan_workload_daily.adjusted_count``

⚠️ **判定权在人**
裁定**只**写 ``adjusted_count``，**永不**改 ``total_count``
（那是机器判定的原始值）。⇒ 机器判定与人工裁定**并存可对照**。

⚠️ **hash 链全程留痕**
每一步操作都追加一条事件，事件里带上一条的 hash。
任何一条被篡改或删除，链校验就会断。
附件的 sha256 也会写进事件 payload ⇒ **证据被换过能被发现**。
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import date, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domain.pan_workload_models import (
    PanWorkloadDaily,
    PanWorkloadFile,
    PanWorkloadReview,
    PanWorkloadReviewArtifact,
    PanWorkloadReviewEvent,
    PanWorkloadReviewItem,
)

logger = logging.getLogger(__name__)

#: 允许的证据 MIME 类型（2026-11-21 业务确认：图片 + 视频 + 文档）
#:
#: ⚠️ **用白名单而不是前缀黑名单**
#: 前缀匹配会放行 ``application/x-msdownload``（exe）、``application/x-sh`` 等
#: **可执行文件** —— 那不是证据，是攻击面。
#: ⇒ 精确列出可信类型，其余一律拒绝。
ALLOWED_MEDIA_TYPES: frozenset[str] = frozenset(
    {
        # 图片
        "image/png", "image/jpeg", "image/jpg", "image/gif", "image/webp",
        "image/bmp", "image/heic", "image/tiff",
        # 视频
        "video/mp4", "video/quicktime", "video/x-msvideo", "video/webm",
        "video/x-matroska", "video/avi", "video/mpeg",
        # 文档
        "application/pdf",
        "text/plain", "text/csv",
        "application/msword",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/vnd.ms-excel",
    }
)

#: 单个附件大小上限（字节）—— 防止有人传 2GB 视频把库撑爆
MAX_ARTIFACT_BYTES = 20 * 1024 * 1024

#: 争议类型
DISPUTE_TYPES = ("missing", "overcounted", "weight", "other")

#: 裁定粒度
SCOPE_DAY = "day"
SCOPE_FILE = "file"


def canonical(value: Any) -> str:
    """规范化 JSON —— 与框架 ``review_center.canonical`` 保持一致。

    ⚠️ ``sort_keys=True`` ⇒ 同样的内容永远得到同样的 hash。
    """
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
        default=lambda x: x.isoformat(),
    )


def digest(value: Any) -> str:
    """内容摘要 —— 与框架 ``review_center.digest`` 保持一致。"""
    return hashlib.sha256(canonical(value).encode()).hexdigest()


class PanWorkloadReviewError(Exception):
    """复核工单业务异常（携带 HTTP 状态码）。"""

    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


class PanWorkloadReviewService:
    """复核工单服务。"""

    def __init__(self, db: Session) -> None:
        self.db = db

    # ─────────────────────────────────────────────────────────────
    # 事件流（hash 链）
    # ─────────────────────────────────────────────────────────────

    def _append_event(
        self,
        review: PanWorkloadReview,
        *,
        actor_id: str,
        event_type: str,
        payload: dict[str, Any],
    ) -> PanWorkloadReviewEvent:
        """追加一条事件 —— **hash 链**。

        ⚠️ **链的构造**
        ``prev_hash`` = 上一条的 ``event_hash``；本条 ``event_hash``
        = ``sha256(规范化的完整内容)``。
        ⇒ 任何一条被改，前后的 hash 就对不上。
        """
        previous = self.db.scalar(
            select(PanWorkloadReviewEvent)
            .where(PanWorkloadReviewEvent.review_id == review.id)
            .order_by(PanWorkloadReviewEvent.seq.desc())
            .limit(1)
        )
        seq = (previous.seq + 1) if previous else 1
        prev_hash = previous.event_hash if previous else ""
        created = datetime.now()
        body = {
            "review_id": review.id,
            "seq": seq,
            "actor_id": actor_id,
            "event_type": event_type,
            "payload": payload,
            "prev_hash": prev_hash,
            "created_at": created.isoformat(),
        }
        # ⚠️ ``created_at`` 已在 body 里（ISO 串，用于算 hash），
        #    但 ORM 列要的是 datetime ⇒ 这里用 dict 覆盖成 datetime 对象。
        row = PanWorkloadReviewEvent(
            **{**body, "created_at": created},
            event_hash=digest(body),
        )
        self.db.add(row)
        self.db.flush()
        return row

    def verify_chain(self, review_id: str) -> dict[str, Any]:
        """校验事件链 + 附件摘要 —— 返回问题清单。

        ⚠️ **口径要诚实**
        应用层只追加 + 本地哈希校验，**不能证明数据库管理员没同时改写整条链**
        （与框架 ``review_center.verify_chain`` 同一句话）。
        """
        issues: list[str] = []
        events = list(
            self.db.scalars(
                select(PanWorkloadReviewEvent)
                .where(PanWorkloadReviewEvent.review_id == review_id)
                .order_by(PanWorkloadReviewEvent.seq)
            )
        )
        previous = ""
        for index, e in enumerate(events, 1):
            body = {
                "review_id": e.review_id,
                "seq": e.seq,
                "actor_id": e.actor_id,
                "event_type": e.event_type,
                "payload": e.payload,
                "prev_hash": e.prev_hash,
                "created_at": e.created_at.isoformat(),
            }
            if e.seq != index:
                issues.append(f"事件序号断裂：期望 {index} 实际 {e.seq}")
            if e.prev_hash != previous:
                issues.append(f"事件链 hash 不连续：{e.id}")
            if digest(body) != e.event_hash:
                issues.append(f"事件摘要异常：{e.id}")
            previous = e.event_hash

        for a in self.db.scalars(
            select(PanWorkloadReviewArtifact).where(
                PanWorkloadReviewArtifact.review_id == review_id
            )
        ).all():
            if hashlib.sha256(a.content).hexdigest() != a.digest:
                issues.append(f"附件摘要异常：{a.id}")

        return {
            "ok": not issues,
            "issues": issues,
            "event_count": len(events),
            "head_hash": previous,
            "guarantee": "应用层只追加及本地哈希校验；独立存证尚需部署，"
            "不能证明数据库管理员未同时改写整条链",
        }

    # ─────────────────────────────────────────────────────────────
    # 查询
    # ─────────────────────────────────────────────────────────────

    def get_review(self, review_id: str) -> PanWorkloadReview:
        row = self.db.get(PanWorkloadReview, review_id)
        if row is None:
            raise PanWorkloadReviewError("复核工单不存在", 404)
        return row

    def list_reviews(
        self,
        *,
        employee_id: str = "",
        status: str = "",
        limit: int = 50,
    ) -> list[PanWorkloadReview]:
        stmt = select(PanWorkloadReview)
        if employee_id:
            stmt = stmt.where(PanWorkloadReview.employee_id == employee_id)
        if status:
            stmt = stmt.where(PanWorkloadReview.status == status)
        return list(
            self.db.scalars(
                stmt.order_by(PanWorkloadReview.created_at.desc()).limit(limit)
            )
        )

    def list_items(self, review_id: str) -> list[PanWorkloadReviewItem]:
        return list(
            self.db.scalars(
                select(PanWorkloadReviewItem)
                .where(PanWorkloadReviewItem.review_id == review_id)
                .order_by(PanWorkloadReviewItem.created_at)
            )
        )

    def list_artifacts(self, review_id: str) -> list[PanWorkloadReviewArtifact]:
        return list(
            self.db.scalars(
                select(PanWorkloadReviewArtifact).where(
                    PanWorkloadReviewArtifact.review_id == review_id
                )
            )
        )

    def list_events(self, review_id: str) -> list[PanWorkloadReviewEvent]:
        return list(
            self.db.scalars(
                select(PanWorkloadReviewEvent)
                .where(PanWorkloadReviewEvent.review_id == review_id)
                .order_by(PanWorkloadReviewEvent.seq)
            )
        )

    def files_of_day(
        self, employee_id: str, day: str
    ) -> list[PanWorkloadFile]:
        """那天系统的所有文件 —— **含被排除的**。

        ⚠️ 被排除的文件也要列出来 ——
        员工争议的常常正是「我的文件被当成封面排除了」。
        """
        return list(
            self.db.scalars(
                select(PanWorkloadFile)
                .where(
                    PanWorkloadFile.employee_id == employee_id,
                    PanWorkloadFile.business_date == day,
                )
                .order_by(PanWorkloadFile.counted.desc(), PanWorkloadFile.pan_path)
            )
        )

    # ─────────────────────────────────────────────────────────────
    # 发起
    # ─────────────────────────────────────────────────────────────

    def create_review(
        self,
        *,
        employee_id: str,
        business_date: str,
        claimed_count: int,
        reason: str,
        scope: str = SCOPE_DAY,
        created_by: str = "",
    ) -> PanWorkloadReview:
        """发起复核工单。

        ⚠️ **快照当时判定值**
        工单创建时把日汇总的当前值**固定下来**。
        之后系统可能重算（规则改了、文件补传了），
        但「当时判定为多少条」不能变 —— 否则无从对账。
        """
        if scope not in (SCOPE_DAY, SCOPE_FILE):
            raise PanWorkloadReviewError(f"裁定粒度非法：{scope}")
        try:
            day = date.fromisoformat(business_date)
        except ValueError as exc:
            raise PanWorkloadReviewError(f"日期格式非法：{business_date}") from exc
        if not reason.strip():
            raise PanWorkloadReviewError("必须填写申诉理由")

        daily = self.db.scalar(
            select(PanWorkloadDaily).where(
                PanWorkloadDaily.employee_id == employee_id,
                PanWorkloadDaily.business_date == day,
            )
        )
        system_count = daily.total_count if daily else 0
        snapshot = {
            "total_count": system_count,
            "file_count": daily.file_count if daily else 0,
            "excluded_count": daily.excluded_count if daily else 0,
            "marker_breakdown": daily.marker_breakdown if daily else {},
            "adjusted_count": daily.adjusted_count if daily else None,
            # ⚠️ **当天是否已有采集数据**
            # 没有时 system_count 必然是 0，但「0 条」和「还没采过」是两回事 ——
            # 前者说明系统认为这人当天没干活，后者说明系统**根本没数据**。
            # 不区分会让员工以为系统判定他 0 条。
            "has_daily": daily is not None,
            "captured_at": datetime.now().isoformat(timespec="seconds"),
        }

        review = PanWorkloadReview(
            employee_id=employee_id,
            business_date=day,
            status="pending",
            scope=scope,
            claimed_count=claimed_count,
            system_count=system_count,
            reason=reason,
            daily_snapshot=snapshot,
            created_by=created_by or employee_id,
        )
        self.db.add(review)
        self.db.flush()
        self._append_event(
            review,
            actor_id=created_by or employee_id,
            event_type="created",
            payload={
                "claimed_count": claimed_count,
                "system_count": system_count,
                "scope": scope,
                "daily_snapshot": snapshot,
            },
        )
        # ⚠️ 日汇总标记为「有异议」—— 面板上要能筛出来
        if daily is not None and daily.status == "auto":
            daily.status = "disputed"
        self.db.flush()
        return review

    def add_item(
        self,
        review_id: str,
        *,
        pan_workload_file_id: str = "",
        manual_name: str = "",
        manual_description: str = "",
        dispute_type: str = "other",
        claimed_weight: int = 1,
        actor_id: str = "",
    ) -> PanWorkloadReviewItem:
        """加一个争议项。

        ⚠️ **两种来源都支持**（2026-11-21 业务确认）
        - ``pan_workload_file_id``：**勾选**系统已列出的文件
        - ``manual_name``：**手填**系统没扫到的（比如漏传）

        ⚠️ **至少要有一个**
        否则这个争议项什么都不是，无法裁定。
        """
        review = self.get_review(review_id)
        if review.status not in ("pending",):
            raise PanWorkloadReviewError("工单已裁定，不能再添加争议项", 409)
        if dispute_type not in DISPUTE_TYPES:
            raise PanWorkloadReviewError(f"争议类型非法：{dispute_type}")
        if not pan_workload_file_id and not manual_name.strip():
            raise PanWorkloadReviewError("必须指定争议文件（勾选或手填文件名）")
        if claimed_weight < 0:
            raise PanWorkloadReviewError("主张条数不能为负")

        item = PanWorkloadReviewItem(
            review_id=review.id,
            pan_workload_file_id=pan_workload_file_id or None,
            manual_name=manual_name.strip(),
            manual_description=manual_description.strip(),
            dispute_type=dispute_type,
            claimed_weight=claimed_weight,
            resolution="pending",
        )
        self.db.add(item)
        self.db.flush()
        self._append_event(
            review,
            actor_id=actor_id or review.created_by,
            event_type="item_added",
            payload={
                "item_id": item.id,
                "pan_workload_file_id": item.pan_workload_file_id,
                "manual_name": item.manual_name,
                "dispute_type": item.dispute_type,
                "claimed_weight": item.claimed_weight,
            },
        )
        return item

    def add_artifact(
        self,
        review_id: str,
        *,
        name: str,
        media_type: str,
        content: bytes,
        review_item_id: str = "",
        actor_id: str = "",
    ) -> PanWorkloadReviewArtifact:
        """上传证据附件。

        ⚠️ **类型与大小都要校验**
        - 只接受图片 / 视频 / 文档
        - 单文件 ≤ 20MB
        ⇒ 防止有人传超大文件把库撑爆，或传可执行文件。

        ⚠️ **内容存库不落盘**
        ⇒ 不会丢文件、不会有路径穿越风险。
        """
        review = self.get_review(review_id)
        if review.status not in ("pending",):
            raise PanWorkloadReviewError("工单已裁定，不能再上传证据", 409)
        if not content:
            raise PanWorkloadReviewError("附件内容为空")
        if len(content) > MAX_ARTIFACT_BYTES:
            raise PanWorkloadReviewError(
                f"附件过大（{len(content) // 1024 // 1024}MB），上限 20MB"
            )
        if media_type.lower() not in ALLOWED_MEDIA_TYPES:
            raise PanWorkloadReviewError(
                f"不支持的证据类型：{media_type}（仅支持图片/视频/文档）"
            )
        if review_item_id:
            item = self.db.get(PanWorkloadReviewItem, review_item_id)
            if item is None or item.review_id != review.id:
                raise PanWorkloadReviewError("争议项不存在", 404)

        artifact = PanWorkloadReviewArtifact(
            review_id=review.id,
            review_item_id=review_item_id or None,
            name=name[:255],
            media_type=media_type[:100],
            content=content,
            digest=hashlib.sha256(content).hexdigest(),
            size_bytes=len(content),
            uploaded_by=actor_id or review.created_by,
        )
        self.db.add(artifact)
        self.db.flush()
        # ⚠️ **附件摘要写进事件** ⇒ 事后换证据能被发现
        self._append_event(
            review,
            actor_id=actor_id or review.created_by,
            event_type="artifact_added",
            payload={
                "artifact_id": artifact.id,
                "review_item_id": artifact.review_item_id,
                "name": artifact.name,
                "media_type": artifact.media_type,
                "digest": artifact.digest,
                "size_bytes": artifact.size_bytes,
            },
        )
        return artifact

    # ─────────────────────────────────────────────────────────────
    # 裁定
    # ─────────────────────────────────────────────────────────────

    def resolve_item(
        self,
        item_id: str,
        *,
        resolution: str,
        final_weight: int | None = None,
        resolution_note: str = "",
        actor_id: str = "",
        expect_review_id: str = "",
    ) -> PanWorkloadReviewItem:
        """裁定单个争议项。

        ⚠️ **三档裁定**
        ``upheld``     成立 —— 员工说得对，按员工主张的条数算
        ``rejected``   不成立 —— 系统判对了，维持原样
        ``need_more``  需补证 —— 挂起，暂不改数

        ⚠️ **``need_more`` 不改数**
        只有 ``upheld`` 才会影响最终条数。

        ⚠️ **``expect_review_id``（2026-11-23 加）**
        路由是 ``/reviews/{review_id}/items/{item_id}/resolve`` ⇒
        **争议项必须属于该工单**。不传则跳过校验（内部调用方用）。
        """
        if resolution not in ("upheld", "rejected", "need_more"):
            raise PanWorkloadReviewError(f"裁定结果非法：{resolution}")
        item = self.db.get(PanWorkloadReviewItem, item_id)
        if item is None:
            raise PanWorkloadReviewError("争议项不存在", 404)
        # ⚠️⚠️ **归属校验**（2026-11-23 加）—— 争议项必须属于该工单。
        #    ⚠️ 不校验 ⇒ 路由里的 ``review_id`` 形同虚设，
        #    主管可以拿 A 工单的 review_id 去裁定 B 工单的争议项。
        if expect_review_id and item.review_id != expect_review_id:
            raise PanWorkloadReviewError("争议项不属于该工单", 404)
        review = self.get_review(item.review_id)
        if review.status != "pending":
            raise PanWorkloadReviewError("工单已裁定", 409)

        item.resolution = resolution
        item.resolution_note = resolution_note.strip()
        if resolution == "upheld":
            item.final_weight = (
                final_weight if final_weight is not None else item.claimed_weight
            )
        elif resolution == "rejected":
            # ⚠️ 不成立 ⇒ 维持系统原值
            item.final_weight = None
        else:
            item.final_weight = None

        self.db.flush()
        self._append_event(
            review,
            actor_id=actor_id,
            event_type="item_resolved",
            payload={
                "item_id": item.id,
                "resolution": resolution,
                "final_weight": item.final_weight,
                "note": item.resolution_note,
            },
        )
        return item

    def compute_final_count(self, review: PanWorkloadReview) -> int:
        """算裁定后的最终条数。

        ⚠️ **两种粒度两种算法**

        ``day``
            直接取主管定的 ``final_count``。

        ``file``
            **系统条数 ± 逐项调整**：
            - ``upheld`` 的「漏算」项 → **加上**员工主张的条数
            - ``upheld`` 的「多算」项 → **减去**多出来的部分
            - ``upheld`` 的「权重不对」项 → 调整为 ``final_weight``
            - ``rejected`` / ``need_more`` → 不动

        ⇒ 公式：``final = system_count + Σ(每个成立项的增量)``
        """
        if review.scope == SCOPE_DAY:
            return review.final_count if review.final_count is not None else review.system_count

        base = review.system_count
        delta = 0
        for item in self.list_items(review.id):
            if item.resolution != "upheld":
                continue
            if item.dispute_type == "missing":
                # ⚠️ 系统漏了 ⇒ 整个文件应算的条数都加上
                delta += item.final_weight if item.final_weight is not None else item.claimed_weight
            elif item.dispute_type == "overcounted":
                # ⚠️ 系统多算了 ⇒ 减去多出来的
                #   final_weight = 该文件实际应算的条数（0 = 完全不该算）
                actual = item.final_weight if item.final_weight is not None else 0
                original = item.claimed_weight
                delta -= max(0, original - actual)
            elif item.dispute_type == "weight":
                # ⚠️ 权重不对 ⇒ 调到正确的值
                target = item.final_weight if item.final_weight is not None else item.claimed_weight
                delta += target - item.claimed_weight
            else:
                delta += item.final_weight if item.final_weight is not None else 0
        return max(0, base + delta)

    def resolve_review(
        self,
        review_id: str,
        *,
        resolution: str,
        final_count: int | None = None,
        resolution_note: str = "",
        actor_id: str = "",
    ) -> PanWorkloadReview:
        """裁定工单 —— **写回日汇总**。

        ⚠️ **只写 ``adjusted_count``，永不改 ``total_count``**
        ``total_count`` 是机器判定的原始值，
        改了就没法回答「系统当初判的是多少、谁改的、为什么改」。

        ⚠️ **按天整体裁定（scope=day）**
        ``final_count`` 必填。

        ⚠️ **逐文件裁定（scope=file）**
        ``final_count`` 可留空 —— 留空则**按逐项裁定自动算**。
        """
        if resolution not in ("approved", "rejected"):
            raise PanWorkloadReviewError(f"裁定结果非法：{resolution}")
        review = self.get_review(review_id)
        if review.status != "pending":
            raise PanWorkloadReviewError("工单已裁定", 409)

        if resolution == "approved":
            if review.scope == SCOPE_DAY:
                if final_count is None:
                    raise PanWorkloadReviewError("按天裁定必须给出最终条数")
                count = final_count
            else:
                count = self.compute_final_count(review)
                if final_count is not None:
                    count = final_count
            if count < 0:
                raise PanWorkloadReviewError("最终条数不能为负")
        else:
            # ⚠️ 驳回 ⇒ **不产生调整值**。
            #    写 ``adjusted_count = system_count`` 等于「确认了机器判定」，
            #    但主管明明说了「不成立」—— 那不是调整，是无中生有的一个数字。
            #    ⇒ 驳回时 ``adjusted_count`` 保持 None（= 没有人工调整）。
            count = review.system_count

        review.status = resolution
        review.resolution = resolution_note.strip()
        review.final_count = count
        review.resolved_at = datetime.now()
        review.updated_at = datetime.now()

        # ⚠️ **写回日汇总** —— 只动 adjusted_count
        daily = self.db.scalar(
            select(PanWorkloadDaily).where(
                PanWorkloadDaily.employee_id == review.employee_id,
                PanWorkloadDaily.business_date == review.business_date,
            )
        )
        if daily is not None:
            if resolution == "approved":
                daily.adjusted_count = count
                # ⚠️ **说明不能是空壳**
                # 只写「复核工单 xxx：」等于没写 —— 事后没人知道为什么改的。
                # 没填说明时至少把「从多少改成多少」记下来。
                note = resolution_note.strip()
                if not note:
                    note = f"系统判定 {review.system_count} 条，裁定为 {count} 条（未填写说明）"
                daily.adjust_reason = f"复核工单 {review.id[:8]}：{note[:200]}"
                daily.status = "confirmed"
            else:
                # ⚠️ 驳回 ⇒ 恢复成「有异议」而不是「已确认」——
                #    机器判定依然有效，但主管明确否了员工的申诉。
                #    ⇒ adjusted_count 保持空（没人工调整），状态标 disputed。
                daily.adjusted_count = None
                daily.adjust_reason = ""
                daily.status = "disputed"

        self.db.flush()
        self._append_event(
            review,
            actor_id=actor_id,
            event_type=resolution,
            payload={
                "final_count": count,
                "system_count": review.system_count,
                "scope": review.scope,
                "note": resolution_note,
                "daily_adjusted": daily.adjusted_count if daily else None,
            },
        )
        return review

    def withdraw(self, review_id: str, *, actor_id: str, note: str = "") -> PanWorkloadReview:
        """员工撤回工单。

        ⚠️ 撤回**不恢复**日汇总的调整值 ——
        若之前已有调整（不可能，撤回只发生在 pending 状态），此处不做特殊处理。
        """
        review = self.get_review(review_id)
        if review.status != "pending":
            raise PanWorkloadReviewError("只有待裁定的工单可以撤回", 409)
        review.status = "withdrawn"
        review.resolution = note.strip()
        review.updated_at = datetime.now()
        self.db.flush()
        self._append_event(
            review,
            actor_id=actor_id,
            event_type="withdrawn",
            payload={"note": note},
        )
        return review

    def review_detail(self, review_id: str) -> dict[str, Any]:
        """工单详情 —— 主管裁定页要看的全部信息。"""
        review = self.get_review(review_id)
        items = self.list_items(review_id)
        artifacts = self.list_artifacts(review_id)
        return {
            "review": review,
            "items": items,
            "artifacts": artifacts,
            "events": self.list_events(review_id),
            "chain": self.verify_chain(review_id),
        }
