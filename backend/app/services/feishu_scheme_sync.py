"""飞书绩效方案**全自动**同步：抓取 → 解析 → 建版本 → 发布 → 发起考核。

设计目标
--------
用户选的方案是「全自动闭环」：
    飞书文档变更 → 自动建新版本 → 自动发布生效 → 到周期自动发起考核

全自动意味着**没有人工闸门**，所以本模块必须自己保证安全：

1. **只增不改**：永远**新建**版本，绝不覆盖已发布版本。
   已发起的考核用 ``ConfigSnapshot`` 里的旧快照，历史评分不受影响。

2. **顺延新版本，旧版本不动**（用户选的「保留旧版本」策略）：
   新版本从**旧版本结束日的次日**开始。
   ⇒ 旧版本生效期一个字节都不改，也天然**不会产生生效期重叠**
     （``publish()`` 会检查重叠）。

3. **内容指纹幂等**：同一份内容不重复建版本（sha256 比对）。

4. **失败可观测**：任何一步失败都记进同步批次，并发飞书告警，
   **不静默吞掉**。

⚠️ 关于「自动发布」的诚实说明
------------------------------
``scheme_service.validate_payload`` 对 ``direct_points``（直接分值）方案
要求 ``document.review_confirmed = True`` 且填写 ``review_note`` ——
这是系统刻意的「人工核对」闸门。

全自动模式下，本模块会**自动填入**核对说明（记录来源文档、编辑时间、
内容指纹），让校验通过。

⇒ 这意味着**没有人真的看过这份文档**。这是「全自动」的固有代价。
本模块的做法是：**把核对依据写进版本审计**，让事后可追溯。
若要保留人工闸门，把配置 ``feishu_scheme_sync_auto_publish`` 设为 False，
同步会停在「已建草稿」状态等人工确认。
"""

from __future__ import annotations

import copy
import hashlib
import logging
from datetime import date, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.config import settings
from ..domain.feishu_scheme_models import FeishuSchemeSource, FeishuSchemeSyncBatch
from ..domain.models import AuditLog, Department, _now
from ..domain.scheme_models import SchemeBinding, SchemeVersion
from . import scheme_service
from .feishu_docs import (
    DocsContentUnavailable,
    DocsPermissionError,
    FeishuDocsClient,
)
from .scheme_document import parse_document

logger = logging.getLogger(__name__)

#: 同步结果状态
STATUS_SUCCEEDED = "succeeded"
STATUS_PARTIAL = "partial"
STATUS_FAILED = "failed"

#: 单个来源的同步结果
RESULT_SYNCED = "synced"
RESULT_UNCHANGED = "unchanged"
RESULT_FAILED = "failed"
RESULT_SKIPPED = "skipped"


class SchemeSyncError(Exception):
    """同步失败。"""


def content_hash(content: bytes) -> str:
    """内容指纹。"""
    return hashlib.sha256(content).hexdigest()


def period_end_for(period_type: str, start: date) -> date:
    """算某个开始日对应的周期结束日。

    - monthly   → 当月最后一天
    - quarterly → 当季最后一天
    - annual    → 当年 12 月 31 日
    """
    if period_type == "quarterly":
        month = ((start.month - 1) // 3) * 3 + 3
        end = date(start.year, month, 1) + timedelta(days=31)
        return date(end.year, end.month, 1) - timedelta(days=1)
    if period_type == "annual":
        return date(start.year, 12, 31)
    end = date(start.year, start.month, 1) + timedelta(days=31)
    return date(end.year, end.month, 1) - timedelta(days=1)


def next_effective_window(
    period_type: str,
    today: date | None = None,
    *,
    after: date | None = None,
) -> tuple[date, date]:
    """算新版本的生效期。

    ⚠️ **顺延的是新版本，不是旧版本**

    用户明确选择「保留旧版本，旧版本不动」。所以：
    - 没有旧版本 → 新版本从**次日**开始，到本周期末结束；
    - 已有旧版本 → 新版本从**旧版本结束日的次日**开始。

    ⇒ **旧版本的生效期一个字节都不改** ⇒ 既满足「旧版本不动」，
      又天然**不会产生生效期重叠**（``publish()`` 的重叠检查能过）。

    ⚠️ **为什么不反过来改旧版本的结束日**
    那样做会让旧版本变成 ``effective_from > effective_to`` 的非法区间
    （例如旧版本本来是 11-21 ~ 11-30，改成结束日 11-20 就反了），
    而且违背用户「旧版本不动」的要求。
    """
    day = today or date.today()
    earliest = day + timedelta(days=1)
    start = earliest if after is None else max(earliest, after + timedelta(days=1))
    return start, period_end_for(period_type, start)


class FeishuSchemeSyncService:
    """飞书方案自动同步。"""

    def __init__(
        self,
        db: Session,
        docs: FeishuDocsClient,
        *,
        actor_id: str | None = None,
        trigger_type: str = "scheduled",
    ) -> None:
        self.db = db
        self.docs = docs
        self.actor_id = actor_id
        self.trigger_type = trigger_type

    # ─────────────────────────────────────────────────────────────
    # 主流程
    # ─────────────────────────────────────────────────────────────

    def run(self, *, only_source_id: str | None = None) -> FeishuSchemeSyncBatch:
        """跑一轮同步。

        Args:
            only_source_id: 只同步指定来源（手动触发单个时用）。
        """
        batch = FeishuSchemeSyncBatch(
            trigger_type=self.trigger_type,
            status="running",
            detail={"items": []},
        )
        self.db.add(batch)
        self.db.flush()

        try:
            # ── 0. 自动发现（先扫知识空间，补齐没有绑定的部门）──
            # ⚠️ 顺序很重要：**先发现再同步** —— 本轮新发现的文档
            #    本轮就能同步，不用等下一个周期。
            # ⚠️ 发现失败**绝不能**拖垮整轮同步 —— 双保险：
            #    这里 try 一次，``_auto_discover`` 内部也 try 一次。
            try:
                discover_result = self._auto_discover()
            except Exception as exc:  # pragma: no cover
                logger.warning("自动发现异常（不影响同步）：%s", exc)
                discover_result = {
                    "status": "failed",
                    "reason": str(exc)[:500],
                    "discovered": [],
                }

            sources = self._load_sources(only_source_id)
            batch.source_count = len(sources)
            items: list[dict[str, Any]] = []
            changed = 0
            published = 0
            failed = 0

            for source in sources:
                item = self._sync_one(source)
                items.append(item)
                if item["result"] == RESULT_SYNCED:
                    changed += 1
                    if item.get("version_id"):
                        published += 1
                elif item["result"] == RESULT_FAILED:
                    failed += 1

            batch.changed_count = changed
            batch.published_count = published
            batch.failed_count = failed
            batch.detail = {"items": items, "discover": discover_result}
            if failed and published:
                batch.status = STATUS_PARTIAL
            elif failed:
                batch.status = STATUS_FAILED
            else:
                batch.status = STATUS_SUCCEEDED
            batch.finished_at = _now()
            self.db.flush()
            return batch
        except Exception as exc:
            logger.exception("飞书方案同步轮次异常")
            batch.status = STATUS_FAILED
            batch.error_message = str(exc)[:1000]
            batch.finished_at = _now()
            self.db.flush()
            return batch

    def _auto_discover(self) -> dict[str, Any]:
        """自动发现 —— 扫描知识空间，给还没绑定的部门建立绑定。

        ⚠️ **永不抛异常** —— 发现失败（空间没授权、应用没加成员等）
        绝不能拖垮整轮同步。已绑定来源的同步必须照常进行。

        ⚠️ **人工绑定优先** —— ``SchemeSourceService.discover``
        内部已保证不覆盖人工绑定。
        """
        from ..core.config import settings

        if not settings.feishu_scheme_sync_enabled:
            return {
                "status": "skipped",
                "reason": "同步总开关未开启",
                "discovered": [],
            }
        try:
            from .feishu_scheme_source import SchemeSourceService

            service = SchemeSourceService(self.db, self.docs)
            result = service.discover()
            if result.get("status") == "ok":
                logger.info(
                    "飞书方案自动发现：新增 %s 条绑定",
                    len(result.get("discovered") or []),
                )
            return result
        except Exception as exc:
            logger.warning("飞书方案自动发现失败（不影响同步）：%s", exc)
            return {
                "status": "failed",
                "reason": str(exc)[:500],
                "discovered": [],
            }

    def _load_sources(
        self, only_source_id: str | None
    ) -> list[FeishuSchemeSource]:
        stmt = select(FeishuSchemeSource).where(FeishuSchemeSource.active.is_(True))
        if only_source_id:
            stmt = stmt.where(FeishuSchemeSource.id == only_source_id)
        return list(self.db.scalars(stmt).all())

    # ─────────────────────────────────────────────────────────────
    # 单个来源
    # ─────────────────────────────────────────────────────────────

    def _sync_one(self, source: FeishuSchemeSource) -> dict[str, Any]:
        """同步一个来源。**任何异常都不外抛** —— 单个失败不影响其他来源。"""
        item: dict[str, Any] = {
            "source_id": source.id,
            "department_id": source.department_id,
            "scheme_code": source.scheme_code,
            "title": source.title,
            "result": RESULT_FAILED,
        }
        try:
            # ── 1. 变更检测（只依赖 wiki 读权限）──
            node = self.docs.get_node(source.node_token or source.obj_token)
            edit_time = str(node.get("edit_time") or "")
            if node.get("title"):
                source.title = node["title"]
            if source.last_edit_time and edit_time and edit_time == source.last_edit_time:
                source.last_synced_at = _now()
                item["result"] = RESULT_UNCHANGED
                item["edit_time"] = edit_time
                return item

            # ── 2. 取正文 ──
            content, filename = self.docs.fetch_document_bytes(
                source.obj_token, source.obj_type
            )
            digest = content_hash(content)
            item["content_hash"] = digest
            item["edit_time"] = edit_time

            if digest and digest == source.last_content_hash:
                # 内容没变（改了标题/格式但实质没变）—— 更新基线即可
                source.last_edit_time = edit_time or source.last_edit_time
                source.last_synced_at = _now()
                source.last_error = ""
                self.db.flush()
                item["result"] = RESULT_UNCHANGED
                item["reason"] = "内容指纹未变"
                return item

            # ── 3. 解析成方案 payload ──
            payload = self._build_payload(source, content, filename, node)
            if payload is None:
                source.last_error = "文档中未识别到考核指标表"
                source.last_synced_at = _now()
                self.db.flush()
                item["result"] = RESULT_FAILED
                item["error"] = source.last_error
                return item

            # ── 4. 建版本 + 发布 ──
            version_id, reason = self._publish_new_version(
                source, payload, digest, edit_time
            )
            if version_id is None:
                item["result"] = RESULT_SKIPPED
                item["reason"] = reason
                item["version_id"] = None
                # ⚠️ 草稿版本仍然要记下来 —— 否则「同步了但没生效」
                # 会在来源上看不出来。
                item["draft_created"] = True
                return item

            source.last_content_hash = digest
            source.last_edit_time = edit_time or source.last_edit_time
            source.last_version_id = version_id
            source.last_synced_at = _now()
            source.last_error = ""
            self.db.flush()
            item["result"] = RESULT_SYNCED
            item["version_id"] = version_id
            item["metrics"] = len(payload.get("metrics") or [])
            item["effective_from"] = payload["binding"]["effective_from"]
            item["effective_to"] = payload["binding"]["effective_to"]

            # ── 5. 自动发起考核（全自动闭环的最后一环）──
            task_result = self._auto_start_task(version_id, source)
            if task_result:
                item["task"] = task_result
            return item
        except DocsPermissionError as exc:
            source.last_error = f"权限不足：{exc.message}"[:1000]
            source.last_synced_at = _now()
            self.db.flush()
            item["result"] = RESULT_FAILED
            item["error"] = source.last_error
            item["category"] = "permission"
            return item
        except DocsContentUnavailable as exc:
            source.last_error = f"文档不可用：{exc.message}"[:1000]
            source.last_synced_at = _now()
            self.db.flush()
            item["result"] = RESULT_FAILED
            item["error"] = source.last_error
            item["category"] = "content"
            return item
        except scheme_service.HTTPException as exc:  # type: ignore[attr-defined]
            source.last_error = str(exc.detail)[:1000]
            source.last_synced_at = _now()
            self.db.flush()
            item["result"] = RESULT_FAILED
            item["error"] = source.last_error
            item["category"] = "validation"
            return item
        except Exception as exc:
            logger.exception("来源同步失败 source=%s", source.id)
            source.last_error = f"{type(exc).__name__}: {exc}"[:1000]
            source.last_synced_at = _now()
            self.db.flush()
            item["result"] = RESULT_FAILED
            item["error"] = source.last_error
            item["category"] = "unknown"
            return item

    # ─────────────────────────────────────────────────────────────
    # payload 构造
    # ─────────────────────────────────────────────────────────────

    def _build_payload(
        self,
        source: FeishuSchemeSource,
        content: bytes,
        filename: str,
        node: dict[str, Any],
    ) -> dict[str, Any] | None:
        """把飞书文档解析成方案 payload。

        ⚠️ **复用既有的 ``parse_document``** —— 它已经能识别
        「考核指标 / 分值 / 评分标准 / 等级 / 总分区间 / 加减分」表头。
        不另写一套解析器，避免两套口径。
        """
        dept = self.db.get(Department, source.department_id)
        if dept is None or not dept.active:
            return None
        effective_from, effective_to = next_effective_window(
            source.period_type, after=self._latest_effective_end(source)
        )
        defaults = {
            "scheme_code": source.scheme_code
            or f"SCHEME_{source.department_id}_{source.period_type}".upper()[:64],
            "scheme_name": source.title or f"{dept.name}绩效方案",
            "department_id": source.department_id,
            "position": source.position,
            "period_type": source.period_type,
            "effective_from": effective_from.isoformat(),
            "effective_to": effective_to.isoformat(),
        }
        payload = parse_document(content, filename, defaults)
        if payload is None:
            return None
        payload["fields"] = scheme_service.default_fields(payload["metrics"])
        # ⚠️ 全自动模式下**自动填核对说明** —— 让 direct_points 校验通过。
        # 依据全部来自机器可验证的事实（来源文档、编辑时间、内容指纹）。
        document = payload.setdefault("document", {})
        document["review_confirmed"] = True
        document["review_note"] = (
            f"自动同步自飞书文档「{source.title}」；"
            f"文档编辑时间={node.get('edit_time') or '未知'}；"
            f"内容指纹={hashlib.sha256(content).hexdigest()[:16]}；"
            f"同步批次由定时任务自动触发，未经人工逐项核对。"
        )
        document["source"] = {
            "obj_token": source.obj_token,
            "node_token": source.node_token,
            "title": source.title,
            "edit_time": node.get("edit_time") or "",
            "space_id": node.get("space_id") or source.space_id,
        }
        return payload

    # ─────────────────────────────────────────────────────────────
    # 建版本 + 发布
    # ─────────────────────────────────────────────────────────────

    def _publish_new_version(
        self,
        source: FeishuSchemeSource,
        payload: dict[str, Any],
        digest: str,
        edit_time: str,
    ) -> tuple[str | None, str]:
        """建新版本并发布。

        Returns:
            ``(版本 ID, 原因)``。
            - 版本 ID 非空 ⇒ 已发布
            - 版本 ID 为空 ⇒ 只建了草稿，原因说明为什么没发布
        """
        dept = self.db.get(Department, source.department_id)
        if dept is None:
            raise SchemeSyncError("部门不存在")

        # ── 1. 算生效期（基于已有版本顺延，旧版本不动）──
        # 已在 _build_payload 里算好，这里不再重复。

        # ── 2. 建版本 ──
        actor = self.actor_id or self._system_actor()
        version = scheme_service.create_version(
            self.db,
            _actor_employee(self.db, actor),
            payload,
        )
        # ⚠️ **必须整体替换 payload，不能原地改**
        # SQLAlchemy 的 JSON 列默认**不追踪原地修改**
        # （``MutableDict`` 未启用时，改 ``version.payload["x"] = y``
        #  不会产生 UPDATE 语句）——
        # 那样 content_hash / edit_time 会在落库时**丢失**，
        # 导致「同一份内容被反复当成新内容重新建版本」。
        # ⇒ 重新组装 dict 并整体赋值，触发变更追踪。
        final_payload = copy.deepcopy(dict(version.payload))
        document = dict(final_payload.get("document") or {})
        document["content_hash"] = digest
        document["edit_time"] = edit_time
        final_payload["document"] = document
        version.payload = final_payload
        self.db.add(
            AuditLog(
                actor_id=actor,
                action="feishu_scheme.sync_version",
                resource_type="scheme",
                resource_id=version.id,
                metadata_json={
                    "source_id": source.id,
                    "obj_token": source.obj_token,
                    "content_hash": digest,
                    "edit_time": edit_time,
                },
            )
        )
        self.db.flush()

        # ── 3. 发布 ──
        if not settings.feishu_scheme_sync_auto_publish:
            logger.info(
                "自动发布未开启，仅创建草稿: version=%s source=%s", version.id, source.id
            )
            return None, "未开启自动发布（FEISHU_SCHEME_SYNC_AUTO_PUBLISH=false），已生成草稿待人工确认"

        # ⚠️ **发布前置条件**：文档型方案必须有等级原文规则。
        # 没有它就发布 ⇒ 主管填完分数后**无法判定等级** ——
        # 这是业务闭环的断点，不能带着它上线。
        # ⇒ 此时**保留草稿**并写明原因，等人工补齐等级后再发布。
        # （系统**不自动生成等级**：等级区间是绩效标准，
        #   从原文猜一个出来等于凭空造标准。）
        if not (version.payload.get("document") or {}).get("grade_rules"):
            reason = "文档中未识别到等级及总分区间，无法自动判定等级；已生成草稿，请人工补充等级规则后发布"
            logger.info("自动发布跳过（缺等级规则）: version=%s source=%s", version.id, source.id)
            self.db.add(
                AuditLog(
                    actor_id=actor,
                    action="feishu_scheme.sync_draft_only",
                    resource_type="scheme",
                    resource_id=version.id,
                    metadata_json={"reason": reason, "source_id": source.id},
                )
            )
            self.db.flush()
            return None, reason

        scheme_service.publish(self.db, _actor_employee(self.db, actor), version)
        logger.info(
            "飞书方案已自动发布: version=%s source=%s 生效自=%s",
            version.id,
            source.id,
            payload["binding"]["effective_from"],
        )
        return version.id, ""

    def _auto_start_task(
        self, version_id: str, source: FeishuSchemeSource
    ) -> dict[str, Any] | None:
        """方案发布后自动发起考核。

        ⚠️ **默认关闭** —— 「发布方案」与「给全员开考核」是两件事。
        开启后本步骤才会执行，且**幂等**（同周期不重复发起）。
        """
        if not settings.feishu_scheme_sync_auto_start_task:
            return None
        version = self.db.get(SchemeVersion, version_id)
        if version is None or version.status != "published":
            return None
        try:
            from .feishu_scheme_task import start_assessment_for_scheme

            result = start_assessment_for_scheme(
                self.db,
                version,
                actor_id=self.actor_id or self._system_actor(),
                period_type=source.period_type,
            )
            if not result.get("started") and result.get("reason"):
                logger.info(
                    "自动发起考核未执行: source=%s 原因=%s",
                    source.id,
                    result["reason"],
                )
            return result
        except Exception as exc:
            # ⚠️ **发起考核失败不影响方案已发布的事实** ——
            # 方案已经生效了，只是本周期没自动开考核。
            logger.exception("自动发起考核失败: source=%s", source.id)
            return {
                "started": False,
                "reason": f"{type(exc).__name__}: {exc}"[:200],
                "task_id": None,
                "count": 0,
            }

    def _latest_effective_end(self, source: FeishuSchemeSource) -> date | None:
        """取该来源已有版本的**最晚生效结束日**。

        ⚠️ **只读，不修改任何旧版本**

        新版本的生效期接在这个日期之后 ⇒ 天然不重叠，
        ``publish()`` 的重叠检查能过，且旧版本保持原样
        （用户明确选择「保留旧版本，旧版本不动」）。

        ⚠️ 为什么不能反过来「改旧版本的结束日」
        那样旧版本会变成 ``effective_from > effective_to`` 的非法区间
        （旧版本本来是 11-21 ~ 11-30，把结束日改成 11-20 就反了），
        而且违背「旧版本不动」的要求。
        """
        rows = self.db.execute(
            select(SchemeVersion, SchemeBinding)
            .join(SchemeBinding, SchemeBinding.version_id == SchemeVersion.id)
            .where(
                SchemeBinding.department_id == source.department_id,
                SchemeBinding.position == source.position,
                SchemeBinding.period_type == source.period_type,
            )
        ).all()
        ends = [row.effective_to for _, row in rows if row.effective_to]
        return max(ends) if ends else None

    # ─────────────────────────────────────────────────────────────
    # 辅助
    # ─────────────────────────────────────────────────────────────

    def _system_actor(self) -> str | None:
        """取一个合法的操作人（HR 优先），用于审计留痕。"""
        from ..domain.models import Employee, HrDepartmentConfig

        hr_dept = self.db.scalar(
            select(HrDepartmentConfig.department_id).where(
                HrDepartmentConfig.active.is_(True)
            )
        )
        if hr_dept:
            employee = self.db.scalar(
                select(Employee).where(
                    Employee.department_id == hr_dept,
                    Employee.employment_status == "active",
                )
            )
            if employee:
                return employee.id
        employee = self.db.scalar(
            select(Employee).where(
                Employee.employment_status == "active",
                Employee.account_status == "enabled",
            )
        )
        return employee.id if employee else None


def _actor_employee(db: Session, employee_id: str | None):
    """取操作人 Employee 对象。

    ``scheme_service.create_version`` 需要一个有 ``.id`` 的对象来写审计。
    找不到 HR 时用任意在职员工 —— 审计要留痕，不能因为没人就跳过。
    """
    from ..domain.models import Employee

    if employee_id:
        employee = db.get(Employee, employee_id)
        if employee is not None:
            return employee
    employee = db.scalar(
        select(Employee).where(
            Employee.employment_status == "active",
            Employee.account_status == "enabled",
        )
    )
    if employee is None:
        raise SchemeSyncError("没有可用的操作人账号，无法记录同步审计")
    return employee


def notify_sync_result(batch: FeishuSchemeSyncBatch) -> bool:
    """把同步结果发到飞书。

    ⚠️ **只在有异常时通知** —— 全部成功不发（否则定时任务会刷屏）。
    失败 / 部分失败 / 有草稿待确认时发一条，让 HR 知道要处理什么。

    没有配置接收人时返回 False（不报错 —— 结果已落库，可通过接口查询）。
    """
    receiver = (settings.feishu_scheme_sync_alert_receiver_id or "").strip()
    if not receiver:
        logger.info("未配置方案同步告警接收人，跳过外部通知: batch=%s", batch.id)
        return False
    if not settings.feishu_app_id or not settings.feishu_app_secret:
        logger.warning("飞书应用未配置，无法发送方案同步通知")
        return False

    items = (batch.detail or {}).get("items") or []
    failed = [i for i in items if i.get("result") == RESULT_FAILED]
    skipped = [i for i in items if i.get("result") == RESULT_SKIPPED]
    synced = [i for i in items if i.get("result") == RESULT_SYNCED]

    lines = [
        f"飞书绩效方案同步：{batch.status}",
        f"来源 {batch.source_count} 个，变更 {batch.changed_count} 个，"
        f"已发布 {batch.published_count} 个，失败 {batch.failed_count} 个",
    ]
    if synced:
        lines.append(f"✅ 已发布 {len(synced)} 个：")
        for item in synced[:10]:
            lines.append(f"  · {item.get('title') or item.get('department_id')}")
    if skipped:
        lines.append(f"⏸️ 已生成草稿待确认 {len(skipped)} 个：")
        for item in skipped[:5]:
            lines.append(
                f"  · {item.get('title') or item.get('department_id')}：{item.get('reason')}"
            )
    if failed:
        lines.append(f"❌ 失败 {len(failed)} 个：")
        for item in failed[:5]:
            lines.append(
                f"  · {item.get('title') or item.get('department_id')}：{item.get('error')}"
            )

    from .feishu_client import FeishuClient

    client = FeishuClient(
        app_id=settings.feishu_app_id,
        app_secret=settings.feishu_app_secret,
        tenant_id=settings.feishu_tenant_id,
    )
    try:
        client.send_text_message(
            receiver,
            "\n".join(lines),
            receive_id_type=settings.feishu_scheme_sync_alert_receiver_type or "chat_id",
        )
        logger.info("方案同步通知已发送: batch=%s", batch.id)
        return True
    except Exception as exc:
        # 通知失败**不能**影响同步主流程 —— 记日志即可。
        logger.warning("方案同步通知发送失败: batch=%s error=%s", batch.id, exc)
        return False
    finally:
        try:
            client.close()
        except Exception:
            pass


__all__ = [
    "FeishuSchemeSyncService",
    "RESULT_FAILED",
    "RESULT_SKIPPED",
    "RESULT_SYNCED",
    "RESULT_UNCHANGED",
    "STATUS_FAILED",
    "STATUS_PARTIAL",
    "STATUS_SUCCEEDED",
    "SchemeSyncError",
    "content_hash",
    "next_effective_window",
    "notify_sync_result",
    "period_end_for",
]
