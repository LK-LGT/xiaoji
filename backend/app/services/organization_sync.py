"""生产可用的飞书组织架构同步服务。

关键语义：
- 幂等：部门、员工、身份映射均按飞书稳定 ID upsert；
- 增量：以同步游标保存上次成功位置；
- 补偿：单条失败独立事务记录，不阻断批次；
- 对账：比较飞书与本地数量并输出差异与数据质量指标；
- 离职：员工置为 exited、账号禁用、token_version 递增；
- 审计：操作人、批次结果和映射变更全程留痕。

离职判定的安全约束（重要）：
飞书通讯录开放接口不提供「按变更时间拉取」的接口，因此本实现中
「远端没拉到这个人」有两种截然不同的含义：

1. 这个人真的离职/被删除了；
2. 这次没拉到（部门接口抖动、应用可见范围变化、拉取失败）。

把 2 误判成 1 会造成严重事故（整部门员工被停用）。
因此本模块引入**快照完整性**概念：

- 任一部门拉取失败 -> 本次快照标记为 ``不完整``；
- 只有 ``全量模式 + 快照完整`` 才允许把「远端缺失」解释为离职；
- 增量模式一律不做离职判定。
"""

from __future__ import annotations

import inspect
import logging
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from ..domain.models import (
    Department,
    Employee,
    EmployeeDepartment,
    FeishuIdentity,
    OrganizationSyncAudit,
    OrganizationSyncBatch,
    OrganizationSyncCursor,
    OrganizationSyncItemError,
)
from .feishu_client import FeishuClient, FeishuError
from .feishu_identity_service import FeishuIdentityService
from .feishu_sync_errors import (
    classify_feishu_error,
    classify_item_failure,
    is_auto_retryable,
)
from .sync_cursor import RESOURCE_DEPARTMENT, RESOURCE_EMPLOYEE, CursorManager

logger = logging.getLogger(__name__)

# 单条失败的最大自动重试次数，超过后进入死信等待人工处理。
MAX_RETRY_COUNT = 5

# 自动重试的退避基数（秒）。第 n 次重试等待 RETRY_BACKOFF_SECONDS[n-1]。
RETRY_BACKOFF_SECONDS = (30, 120, 600, 1800, 3600)

# 批次终态提交遇到写锁冲突时的重试次数与退避基数（秒）。
_FINAL_COMMIT_RETRIES = 5
_FINAL_COMMIT_BACKOFF_SECONDS = 0.5


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _resolve_employment_status(status: dict[str, Any] | None) -> str:
    """把飞书的 ``status`` 对象翻译成本地的在职状态。

    飞书员工状态有 5 个布尔位，语义各不相同：

    ==================== ================ ==========================
    字段                  含义             本地状态
    ==================== ================ ==========================
    ``is_exited``        离职（含主动     ``exited``
                         退出后自动离职）
    ``is_resigned``      主动离职          同 ``exited``
    ``is_frozen``        冻结（停用）      ``inactive``
    ``is_activated``     已激活            否则 ``inactive``
    ``is_unjoin``        未加入企业        ``inactive``
    ==================== ================ ==========================

    ⚠️ **为什么必须区分 ``is_resigned`` 和 ``is_frozen``**：
    两者都会让员工无法登录，但**含义完全不同** ——
    - ``is_resigned``（离职）→ 账号**永久禁用**，需要走复职流程；
    - ``is_frozen``（冻结）→ 只是临时停用，**解冻后应能自动恢复**。

    如果把冻结一律当成离职，解冻后这个员工就永远登录不了了 ——
    而飞书那边他明明还是在职的。这属于「本地状态比事实源更严」，
    会造成大量误伤。

    ⚠️ 原来的实现只看 ``is_exited`` / ``is_activated``，
    忽略了 ``is_resigned`` 与 ``is_frozen``：
    - 主动离职但 ``is_exited`` 还没翻的窗口期 → 员工**仍可登录**；
    - 冻结的员工 → 状态仍是 ``active``，**照样能登录**。

    两条都是实打实的安全漏洞。

    实现说明
    --------
    判定规则已抽到 ``app.services.offboarding_policy.OffboardingPolicy``，
    便于业务确认口径后**只改配置不改代码**。本函数的默认行为
    与抽取前**完全一致**（``is_unjoin`` 仍按 ``inactive`` 处理）。
    """
    from .offboarding_policy import OffboardingPolicy

    return OffboardingPolicy.from_settings().decide(status).status


def _retry_backoff_seconds(attempt: int) -> int:
    """按重试次数返回退避秒数（超出上限取最大值）。"""
    index = max(0, min(attempt - 1, len(RETRY_BACKOFF_SECONDS) - 1))
    return RETRY_BACKOFF_SECONDS[index]


class OrganizationSyncService:
    """组织架构同步服务。"""

    def __init__(
        self,
        db: Session,
        feishu_client: FeishuClient,
        *,
        mode: str = "full",
        triggered_by: str | None = None,
        trigger_type: str = "manual",
        session_factory: Any = None,
    ) -> None:
        self.db = db
        self.feishu_client = feishu_client
        self.mode = mode
        self.triggered_by = triggered_by
        self.trigger_type = trigger_type
        self.identity_service = FeishuIdentityService(db)
        # 游标管理器（R17 / 需求2）—— 让增量同步真的能续传。
        self.cursors = CursorManager(db)
        # 批次终态兜底提交用的独立会话工厂（测试可注入）。
        self._session_factory = session_factory
        # 本次拉取到的员工（供 _sync_employees / _reconcile 使用）。
        self._current_employees: list[dict[str, Any]] = []
        # 本次拉取失败的部门；非空表示快照不完整。
        self._failed_departments: list[str] = []
        # 本次「拉取阶段」失败的部门（员工列表拉不到）。
        # ⚠️ 与 `_failed_departments` 的区别：
        #    前者是「拉取阶段失败」（部门接口拿不到这个部门的员工），
        #    后者是「写入阶段失败」（员工拿到了但写库失败）。
        #    两者都会让快照不完整，但统计口径不同。
        self._failed_fetch_departments: list[str] = []
        # 本次快照是否完整。
        self._snapshot_complete = False

    @staticmethod
    def _default_session_factory() -> Any:
        """默认的独立会话工厂：走全局 sessionmaker。"""
        from ..core.db import get_sessionmaker

        return get_sessionmaker()

    # ─────────────────────────────────────────────────────────────
    # 事务边界
    # ─────────────────────────────────────────────────────────────

    def _savepoint(self) -> Any:
        """开启一个 SAVEPOINT 子事务。

        为什么需要它（真实库 92 条 FK 报错的根因）：
        部门写入用 ``flush()``（不提交），员工写入在**同一个事务**里。
        某条员工插入失败时若直接 ``rollback()``，会把**尚未提交的部门插入
        一并撤销** —— 于是后续员工的 ``department_id`` 外键目标消失，
        「1 条失败」被放大成「全员失败」。

        SAVEPOINT 让每条记录的写入落在自己的子事务里：
        单条失败只回滚该 savepoint，外层事务里已写入的数据全部保留。
        """
        return self.db.begin_nested()

    def _rollback_savepoint(self, savepoint: Any) -> None:
        """回滚到指定 SAVEPOINT —— 只撤销这一条记录的写入。"""
        if savepoint is None:
            return
        try:
            savepoint.rollback()
        except Exception:  # pragma: no cover - savepoint 已失效时兜底
            logger.warning("SAVEPOINT 回滚失败，继续处理后续记录", exc_info=True)

    def _commit_final(self, batch: OrganizationSyncBatch) -> None:
        """提交批次终态，带锁冲突重试。

        为什么必须重试（实测踩坑）：
        批次终态写在 ``finally`` 块里。若这里的 commit 因 SQLite 写锁
        冲突失败，异常会**覆盖**正常返回流程，批次的 ``status`` 写不进
        库 —— 批次永远停在 ``running``，前端一直转圈。

        所以：commit 失败先回滚再重试；重试仍失败则用**独立会话**
        直连落库，确保批次一定落到终态。
        """
        for attempt in range(_FINAL_COMMIT_RETRIES):
            try:
                self.db.commit()
                return
            except OperationalError as exc:
                # 仅对「锁冲突」重试；其它数据库错误直接抛。
                if "locked" not in str(exc).lower() and "busy" not in str(exc).lower():
                    logger.error("批次终态提交失败（非锁冲突）: %s", self._safe_error(exc))
                    raise
                logger.warning(
                    "批次终态提交遇到写锁冲突，第 %s 次重试: %s",
                    attempt + 1,
                    self._safe_error(exc),
                )
                try:
                    self.db.rollback()
                except Exception:  # pragma: no cover - 回滚失败兜底
                    pass
                time.sleep(_FINAL_COMMIT_BACKOFF_SECONDS * (attempt + 1))

        # 重试用尽：用独立会话直连落库，保证批次不会永远停在 running。
        logger.error("批次终态提交重试耗尽，改用独立会话落库: batch_id=%s", batch.id)
        self._commit_final_in_new_session(batch)

    def _commit_final_in_new_session(self, batch: OrganizationSyncBatch) -> None:
        """用独立数据库会话把批次终态写进去（兜底路径）。"""
        try:
            factory = self._session_factory or self._default_session_factory()
            fresh = factory()
            try:
                row = fresh.get(OrganizationSyncBatch, batch.id)
                if row is None:
                    return
                row.status = batch.status
                row.error_message = batch.error_message
                row.error_category = batch.error_category
                row.phase = "finished"
                row.phase_detail = None
                row.departments_synced = batch.departments_synced
                row.departments_skipped = batch.departments_skipped
                row.departments_failed = batch.departments_failed
                row.employees_synced = batch.employees_synced
                row.employees_skipped = batch.employees_skipped
                row.employees_failed = batch.employees_failed
                row.finished_at = batch.finished_at
                row.duration_ms = batch.duration_ms
                row.reconciliation_json = batch.reconciliation_json
                fresh.commit()
            finally:
                fresh.close()
        except Exception:  # pragma: no cover - 兜底路径，失败只记录
            logger.exception("兜底提交批次终态失败: batch_id=%s", batch.id)

    # ─────────────────────────────────────────────────────────────
    # 主流程
    # ─────────────────────────────────────────────────────────────

    def sync_all(self, batch: OrganizationSyncBatch | None = None) -> dict[str, Any]:
        """执行同步，返回批次统计与对账结果。

        增量模式下的语义：
        飞书通讯录开放接口不提供「按变更时间拉取」的接口，因此这里的
        「增量」指的是「按部门续传 + 只 upsert 有变化的实体」：

        - 部门：游标记录已同步到的部门 ID，续传时跳过已处理的部门；
        - 员工：按部门逐个拉取，游标记录已同步到的部门 ID；
        - 删除判定：只有全量且快照完整时才允许把「远端缺失」解释为离职。

        ``batch`` 参数：复用调用方已建好的批次行。
        ⚠️ 以前这里总是**新建**一个批次，于是「路由建的那条」与
        「实际执行的那条」是两条不同的记录 —— 前者永远停在 running，
        前端一直转圈，且「已有任务在运行」会把后续触发全部挡住。
        """
        if batch is None:
            batch = OrganizationSyncBatch(
                batch_no=f"ORG-{datetime.now(timezone.utc):%Y%m%d}-{uuid.uuid4().hex[:10]}",
                status="running",
                mode=self.mode,
                trigger_type=self.trigger_type,
                triggered_by=self.triggered_by,
                phase="running",
                phase_detail="正在初始化同步",
            )
            self.db.add(batch)
            self.db.commit()
        else:
            # 复用既有批次：接管它，把状态推进到 running。
            batch.status = "running"
            batch.phase = "running"
            batch.phase_detail = "正在初始化同步"
        self.db.refresh(batch)
        started = _now()

        stats: dict[str, int] = {
            "departments_synced": 0,
            "departments_skipped": 0,
            "departments_failed": 0,
            "employees_synced": 0,
            "employees_skipped": 0,
            "employees_failed": 0,
        }

        reconciliation: dict[str, Any] | None = None

        try:
            # ── 拉取部门（带分页游标续传）────────────────────────────
            self._set_phase(batch, "正在拉取部门树")
            dept_state = self.cursors.read(RESOURCE_DEPARTMENT)
            # ⚠️ 只有**上次拉取中断**（还残留着 page_token）才续传。
            #    正常跑完一轮时 page_token 已被清空 —— 带着它续传会
            #    拿一个已失效的 token 去请求 → 400。
            resume_token = dept_state.page_token if self.mode == "incremental" else None

            departments = self._fetch_departments_with_cursor(batch, resume_token)
            self._set_phase(batch, f"已拉取 {len(departments)} 个部门，正在拉取员工")

            # 按游标切分：已处理过的部门跳过，只处理增量部分。
            to_process, skipped = self.cursors.departments_to_process(
                departments, mode=self.mode
            )
            stats["departments_skipped"] += len(skipped)

            # 部门接口的 leader_user_id 实际返回 open_id，
            # 必须先建立 open_id -> user_id 映射才能与 Employee.id 对齐。
            # ⚠️ 员工拉取也要覆盖**全部**部门 —— 增量模式下即使部门本身
            #    已处理过，其下员工也可能刚发生变化。
            self._current_employees = self._fetch_employees_for_departments(
                departments, batch=batch
            )

            # ⚠️ 拉取阶段失败的部门要计入统计 + 落错误记录。
            #    否则批次会显示「ok」、对账看起来干净，
            #    但这些部门的员工其实一个都没同步 ——
            #    运维完全看不出「少了半个部门」。
            for failed_dept_id in self._failed_fetch_departments:
                stats["departments_failed"] += 1
                self._record_error(
                    batch,
                    "department",
                    failed_dept_id,
                    f"部门员工列表拉取失败：{failed_dept_id}",
                    commit=False,
                )

            identity_map = self._build_identity_map(self._current_employees)

            self._set_phase(batch, "正在写入部门")
            self._sync_departments(batch, to_process, stats, identity_map)

            # 先同步部门，确保员工外键目标存在。
            self._set_phase(batch, "正在写入员工")
            self._sync_employees(batch, stats)

            snapshot_complete = self._snapshot_is_complete()
            reconciliation = self._reconcile(departments, self._current_employees)
            batch.reconciliation_json = reconciliation
            batch.status = (
                "partial_failed"
                if stats["employees_failed"] or stats["departments_failed"]
                else "ok"
            )

            # 推进游标：只在对应资源处理成功后推进，失败保留旧位置以便续传。
            # ⚠️ **有失败时不推进** —— 推进了就等于把失败的数据跳过，
            #    下次增量同步不会再碰它们，缺口就永久留下了。
            if not stats["employees_failed"] and not stats["departments_failed"]:
                self._advance_cursors(batch, departments, snapshot_complete)
            else:
                logger.warning(
                    "本批次存在失败记录，游标不推进（保留断点以便下次续传）, "
                    "departments_failed=%s employees_failed=%s",
                    stats["departments_failed"],
                    stats["employees_failed"],
                )
        except Exception as exc:
            # 只记录脱敏后的消息：原始异常可能包含 App Secret / Access Token，
            # logger.exception 会把完整 traceback（含异常消息）写入日志。
            safe_message = self._safe_error(exc)
            logger.error(
                "飞书组织同步批次失败, batch_id=%s error=%s",
                batch.id,
                safe_message,
            )
            batch.status = "failed"
            batch.error_message = safe_message
            batch.error_category = classify_feishu_error(exc)
            reconciliation = None
        finally:
            batch.phase = "finished"
            batch.phase_detail = None
            batch.departments_synced = stats["departments_synced"]
            batch.departments_skipped = stats["departments_skipped"]
            batch.departments_failed = stats["departments_failed"]
            batch.employees_synced = stats["employees_synced"]
            batch.employees_skipped = stats["employees_skipped"]
            batch.employees_failed = stats["employees_failed"]
            batch.finished_at = _now()
            batch.duration_ms = int((batch.finished_at - started).total_seconds() * 1000)
            self._audit(
                batch,
                action="organization_sync",
                result=batch.status,
                detail={
                    **stats,
                    "mode": self.mode,
                    "trigger_type": self.trigger_type,
                    "actor_id": self.triggered_by,
                    "snapshot_complete": self._snapshot_is_complete(),
                    "error_category": batch.error_category,
                },
            )
            self._commit_final(batch)

        result: dict[str, Any] = {
            "batch_id": batch.id,
            "batch_no": batch.batch_no,
            "status": batch.status,
            "snapshot_complete": self._snapshot_is_complete(),
            **stats,
        }
        if reconciliation is not None:
            result["reconciliation"] = reconciliation
        return result

    # ─────────────────────────────────────────────────────────────
    # 游标与增量
    # ─────────────────────────────────────────────────────────────

    def _fetch_departments_with_cursor(
        self,
        batch: OrganizationSyncBatch,
        resume_token: str | None,
    ) -> list[dict[str, Any]]:
        """拉取部门树，并在**每翻一页**时持久化分页游标。

        ⚠️ 为什么每页都要存：
        一个批次可能处理上千个部门 / 几万员工，中途失败（网络抖动、
        数据库锁、进程重启）时，如果只在最后存游标，
        下次就得**从头重拉**所有已成功的页。

        每页持久化后，中断最多损失**一页**的工作量。

        ⚠️ 兼容性：若客户端不支持 ``on_page_token`` 回调
        （旧版客户端 / 测试替身），退化为一次性拉取，行为不变。
        """
        supports_cursor = "on_page_token" in inspect.signature(
            self.feishu_client.fetch_departments
        ).parameters
        if not supports_cursor:
            return self.feishu_client.fetch_departments()

        def _persist(token: str) -> None:
            # page_token 为空 = 已拉完最后一页 → 清空游标，
            # 下次从头开始（而非拿失效 token 去续传）。
            self.cursors.save_page_token(
                RESOURCE_DEPARTMENT, token or None, batch_id=batch.id
            )
            # 每页提交一次 —— 中断时进度已经落库。
            # ⚠️ 这里 commit 只提交**游标**的变更；业务数据还在
            #    各自独立的 savepoint 里，最后统一提交。
            try:
                self.db.commit()
            except Exception as exc:  # pragma: no cover
                self.db.rollback()
                logger.warning("持久化部门分页游标失败: %s", self._safe_error(exc))

        return self.feishu_client.fetch_departments(
            start_page_token=resume_token, on_page_token=_persist
        )

    def _get_cursor(self, resource_type: str) -> OrganizationSyncCursor | None:
        """读取指定资源的同步游标。"""
        return self.db.execute(
            select(OrganizationSyncCursor).where(
                OrganizationSyncCursor.resource_type == resource_type
            )
        ).scalar_one_or_none()

    def _save_cursor(
        self,
        resource_type: str,
        *,
        cursor_value: str | None = None,
        page_token: str | None = None,
        checkpoint_value: str | None = None,
        snapshot_complete: bool | None = None,
        batch_id: str | None = None,
    ) -> None:
        """保存（并推进）同步游标。

        游标只在对应资源**成功处理**后才推进，
        失败时保留旧位置以便下次续传。
        """
        cursor = self._get_cursor(resource_type)
        if cursor is None:
            cursor = OrganizationSyncCursor(resource_type=resource_type)
            self.db.add(cursor)
            self.db.flush()

        if cursor_value is not None:
            cursor.cursor_value = cursor_value
        if page_token is not None:
            cursor.page_token = page_token
        if checkpoint_value is not None:
            cursor.checkpoint_value = checkpoint_value
        if snapshot_complete is not None:
            cursor.snapshot_complete = snapshot_complete
        if batch_id is not None:
            cursor.last_batch_id = batch_id
        cursor.updated_at = _now()
        self.db.flush()

    def _reset_cursor(self, resource_type: str) -> None:
        """重置游标（下次从头开始）。"""
        cursor = self._get_cursor(resource_type)
        if cursor is None:
            return
        cursor.cursor_value = None
        cursor.page_token = None
        cursor.checkpoint_value = None
        cursor.snapshot_complete = False
        self.db.flush()

    def _advance_cursors(
        self,
        batch: OrganizationSyncBatch,
        departments: list[dict[str, Any]],
        snapshot_complete: bool,
    ) -> None:
        """推进部门与员工游标。

        规则：
        - 部门游标：记录本次已处理的最后一个部门 ID；
        - 员工游标：记录本次已处理的最后一个部门 ID（员工按部门拉取，
          因此续传单位是部门）；
        - ``snapshot_complete`` 只有在全量且快照完整时才置 True。
          它是「远端缺失 = 离职/删除」这一判定的唯一前提。

        ⚠️ **只有处理成功才推进**。批次里有失败时游标不推进 ——
        下次从旧位置重来，失败的数据不会被跳过。
        """
        last_dept_id = ""
        for dept in departments:
            dept_id = str(dept.get("department_id") or "").strip()
            if dept_id:
                last_dept_id = dept_id

        complete = bool(snapshot_complete and self.mode == "full")
        try:
            self.cursors.advance_checkpoint(
                RESOURCE_DEPARTMENT,
                last_dept_id or None,
                batch_id=batch.id,
                snapshot_complete=complete,
            )
            self.cursors.advance_checkpoint(
                RESOURCE_EMPLOYEE,
                last_dept_id or None,
                batch_id=batch.id,
                snapshot_complete=complete,
            )
            self.cursors.commit()
        except Exception as exc:  # pragma: no cover - 游标写入失败不应影响主流程
            self.db.rollback()
            logger.warning("推进同步游标失败: %s", self._safe_error(exc))

    def _fetch_employees_for_departments(
        self,
        departments: list[dict[str, Any]],
        *,
        batch: OrganizationSyncBatch | None = None,
    ) -> list[dict[str, Any]]:
        """按部门拉取员工，返回去重后的员工列表。

        单一部门拉取失败不会中断整体；失败的部门会被记录，
        并导致本次快照被标记为**不完整**（因此本批次不会停用任何员工）。

        兼容性：若客户端未实现 ``fetch_employees_in_department``
        （例如测试替身或旧版客户端），退回到一次性 ``fetch_employees``，
        此时无法按部门定位失败来源，快照按完整处理。

        ⚠️ 分页续传：单个部门的员工可能跨很多页。每翻一页都会持久化
        ``员工:部门ID`` 形式的分页游标，中断后从断点继续，
        不必重拉该部门已成功的页。
        """
        if not hasattr(self.feishu_client, "fetch_employees_in_department"):
            employees = self.feishu_client.fetch_employees(departments=departments)
            self._failed_departments = []
            self._snapshot_complete = True
            return list(employees)

        employees: dict[str, dict[str, Any]] = {}
        failed_departments: list[str] = []

        for dept in departments:
            dept_id = str(dept.get("department_id") or "").strip()
            if not dept_id:
                continue
            try:
                dept_employees = self._fetch_employees_for_one_department(
                    dept_id, batch=batch
                )
            except FeishuError as exc:
                failed_departments.append(dept_id)
                logger.warning(
                    "飞书部门员工拉取失败, department_id=%s, 原因=%s", dept_id, exc.message
                )
                # ⚠️ 这条失败**必须**计入统计并落错误记录。
                #    否则：批次状态是「ok」、对账看起来干净，
                #    但这个部门的员工其实一个都没同步 ——
                #    运维完全看不出「少了半个部门」。
                #    （这条路径是「拉取阶段」失败，与
                #    `_sync_departments` 里的「写入阶段」失败互补。）
                self._failed_fetch_departments.append(dept_id)
                continue
            for emp in dept_employees:
                key = str(emp.get("user_id") or emp.get("open_id") or "")
                if not key:
                    continue
                if key in employees:
                    existing = employees[key].get("department_ids") or []
                    for dep_id in emp.get("department_ids") or []:
                        if dep_id not in existing:
                            existing.append(dep_id)
                    employees[key]["department_ids"] = existing
                else:
                    employees[key] = emp

        self._failed_departments = failed_departments
        # 快照不完整：禁止本批次判定离职/删除。
        self._snapshot_complete = not failed_departments
        return list(employees.values())

    def _fetch_employees_for_one_department(
        self,
        dept_id: str,
        *,
        batch: OrganizationSyncBatch | None = None,
    ) -> list[dict[str, Any]]:
        """拉取单个部门的员工，带分页游标续传。

        游标键是 ``员工:<部门ID>`` —— 每个部门独立记录分页进度，
        这样一个部门失败不会影响其它部门的续传位置。
        """
        resource = f"{RESOURCE_EMPLOYEE}:{dept_id}"
        supports_cursor = "on_page_token" in inspect.signature(
            self.feishu_client.fetch_employees_in_department
        ).parameters
        if not supports_cursor:
            return self.feishu_client.fetch_employees_in_department(dept_id)

        state = self.cursors.read(resource)
        resume_token = state.page_token if self.mode == "incremental" else None

        def _persist(token: str) -> None:
            self.cursors.save_page_token(
                resource, token or None, batch_id=batch.id if batch else None
            )
            try:
                self.db.commit()
            except Exception as exc:  # pragma: no cover
                self.db.rollback()
                logger.warning("持久化员工分页游标失败: %s", self._safe_error(exc))

        try:
            users = self.feishu_client.fetch_employees_in_department(
                dept_id, start_page_token=resume_token, on_page_token=_persist
            )
        except FeishuError:
            # ⚠️ 失败时**保留**分页游标（不推进）——
            #    下次从这一页重来，而不是跳过整批员工。
            raise
        # 该部门拉完了 → 清掉它的分页游标（下次从头拉这个部门）。
        self.cursors.save_page_token(resource, None, batch_id=batch.id if batch else None)
        return users

    def _snapshot_is_complete(self) -> bool:
        """本次拉取是否构成完整快照。

        完整快照是「远端缺失 = 离职/删除」这一判定的唯一前提。
        """
        return bool(self._snapshot_complete)

    def _set_phase(self, batch: OrganizationSyncBatch, detail: str | None) -> None:
        """更新批次阶段，供前端展示进度。"""
        batch.phase = "running"
        batch.phase_detail = detail
        self.db.commit()

    # ─────────────────────────────────────────────────────────────
    # 部门同步
    # ─────────────────────────────────────────────────────────────

    @staticmethod
    def _build_identity_map(employees: list[dict[str, Any]]) -> dict[str, str]:
        """建立 open_id / union_id -> user_id 映射。

        飞书部门接口的 leader_user_id 字段实际返回 open_id，
        而员工主键统一使用 user_id，同步前必须先归一。
        """
        mapping: dict[str, str] = {}
        for item in employees:
            user_id = str(item.get("user_id") or "").strip()
            if not user_id:
                continue
            for key in ("open_id", "union_id"):
                value = str(item.get(key) or "").strip()
                if value:
                    mapping[value] = user_id
        return mapping

    def _resolve_leader_id(
        self, raw_leader: Any, identity_map: dict[str, str]
    ) -> str | None:
        """把部门主管 ID 归一到本地员工主键（user_id）。"""
        if not raw_leader:
            return None
        value = str(raw_leader).strip()
        if not value:
            return None
        resolved = identity_map.get(value)
        if resolved:
            return resolved
        # 已经是 user_id 时保持原值。
        if self.db.get(Employee, value):
            return value
        return value

    def _sync_departments(
        self,
        batch: OrganizationSyncBatch,
        departments: list[dict[str, Any]],
        stats: dict[str, int],
        identity_map: dict[str, str] | None = None,
    ) -> None:
        for item in departments:
            dept_id = str(item.get("department_id") or "").strip()
            if not dept_id:
                stats["departments_failed"] += 1
                self._record_error(batch, "department", None, "部门缺少 department_id")
                continue
            savepoint = None
            try:
                savepoint = self._savepoint()
                changed = self._upsert_department(batch, item, identity_map or {})
                if changed:
                    stats["departments_synced"] += 1
                else:
                    stats["departments_skipped"] += 1
                savepoint.commit()
            except Exception as exc:
                # 只回滚这一条部门，不能连带撤销外层事务里已写入的数据。
                self._rollback_savepoint(savepoint)
                stats["departments_failed"] += 1
                self._record_error(
                    batch,
                    "department",
                    dept_id,
                    self._safe_error(exc),
                    item,
                    commit=False,
                )

        # 只有完整目录视图才能判定“飞书中已删除”。
        # 增量模式不能误删；快照不完整（有部门拉取失败）时同样不能删。
        if self.mode == "full" and self._snapshot_is_complete():
            local_rows = self.db.execute(
                select(Department).where(
                    Department.feishu_deleted_at.is_(None),
                    Department.last_seen_batch_id != batch.id,
                )
            ).scalars().all()
            for dept in local_rows:
                dept.active = False
                dept.feishu_deleted_at = _now()
                dept.last_seen_batch_id = batch.id
                stats["departments_skipped"] += 1
        elif self.mode == "full":
            logger.warning("本批次快照不完整，跳过部门删除判定（可能有部门拉取失败）")

    @staticmethod
    def _is_department_active(status: Any) -> bool:
        """判断部门是否启用。

        飞书部门接口的 status 字段实际返回 dict：
        {"is_deleted": False}；旧版接口可能返回 1/2 数字。
        两种形态都要正确处理。
        """
        if status is None:
            return True
        if isinstance(status, dict):
            if status.get("is_deleted"):
                return False
            if "status" in status:
                return int(status["status"]) == 1
            return True
        if isinstance(status, bool):
            return status
        try:
            return int(status) == 1
        except (TypeError, ValueError):
            return True

    def _upsert_department(
        self,
        batch: OrganizationSyncBatch,
        item: dict[str, Any],
        identity_map: dict[str, str] | None = None,
    ) -> bool:
        dept_id = str(item["department_id"])
        dept = self.db.get(Department, dept_id)
        if dept is None:
            dept = Department(
                id=dept_id,
                name=str(item.get("name") or "Unknown"),
            )
            self.db.add(dept)
        else:
            self.db.flush()

        parent_id = item.get("parent_department_id")
        if parent_id and str(parent_id) not in {"0", dept_id}:
            if not self.db.get(Department, str(parent_id)):
                # 父部门可能尚未同步，先创建占位，后续批次会补齐。
                self.db.add(
                    Department(
                        id=str(parent_id),
                        name=str(item.get("parent_department_name") or str(parent_id)),
                        active=False,
                    )
                )
                self.db.flush()

        new_values = {
            "name": str(item.get("name") or dept.name or "Unknown"),
            "parent_id": str(parent_id) if parent_id and str(parent_id) != "0" else None,
            "leader_id": self._resolve_leader_id(
                item.get("leader_user_id"), identity_map or {}
            ),
            "active": self._is_department_active(item.get("status")),
            "feishu_deleted_at": None,
        }
        changed = any(getattr(dept, key) != value for key, value in new_values.items())
        for key, value in new_values.items():
            setattr(dept, key, value)
        self.db.flush()
        return changed

    # ─────────────────────────────────────────────────────────────
    # 员工同步
    # ─────────────────────────────────────────────────────────────

    def _sync_employees(
        self,
        batch: OrganizationSyncBatch,
        stats: dict[str, int],
    ) -> None:
        """同步员工。

        员工存在 manager_id 自引用外键，必须分两轮：
        第一轮只写员工自身字段（manager_id 暂置空），
        第二轮回填 manager_id，避免因上级尚未落库导致外键失败，
        也避免单条失败回滚整个批次已写入的数据。
        """
        employees = self._current_employees
        # 第一轮：写入员工自身数据，暂不设置 manager_id。
        pending: list[tuple[str, str | None]] = []
        for item in employees:
            user_id = str(item.get("user_id") or "").strip()
            if not user_id:
                stats["employees_failed"] += 1
                self._record_error(batch, "employee", None, "员工缺少 user_id")
                continue
            savepoint = None
            try:
                savepoint = self._savepoint()
                changed = self._upsert_employee(batch, item, defer_manager=True)
                if changed:
                    stats["employees_synced"] += 1
                else:
                    stats["employees_skipped"] += 1
                manager_id = (
                    str(item.get("leader_user_id")) if item.get("leader_user_id") else None
                )
                pending.append((user_id, manager_id))
                savepoint.commit()
            except Exception as exc:
                # 单条失败不能阻断批次，也不应回滚其他已成功的数据。
                # 只回滚当前这条记录的 SAVEPOINT —— 部门写入和已成功的
                # 员工都保留在外层事务里（修复前 rollback 会把它们一并撤销，
                # 导致后续员工全部 FOREIGN KEY 失败）。
                self._rollback_savepoint(savepoint)
                stats["employees_failed"] += 1
                self._record_error(
                    batch,
                    "employee",
                    user_id,
                    self._safe_error(exc),
                    item,
                    commit=False,
                )

        # 第二轮：回填 manager_id（此时所有员工均已落库）。
        # manager_id 的变化不额外计数，避免同一员工被统计两次。
        for user_id, manager_id in pending:
            if not manager_id:
                continue
            savepoint = None
            try:
                savepoint = self._savepoint()
                employee = self.db.get(Employee, user_id)
                if employee is None:
                    savepoint.commit()
                    continue
                if employee.manager_id != manager_id:
                    employee.manager_id = manager_id
                    self.db.flush()
                savepoint.commit()
            except Exception as exc:
                self._rollback_savepoint(savepoint)
                stats["employees_failed"] += 1
                self._record_error(
                    batch,
                    "employee",
                    user_id,
                    self._safe_error(exc),
                    None,
                    commit=False,
                )

        # 第三轮：处理「远端已不存在」的员工（离职/删除）。
        # 只在**完整快照**下才允许执行 —— 否则某个部门接口抖动
        # 就会把整个部门的人误判为离职。
        self._deactivate_missing_employees(batch, stats)

    def _deactivate_missing_employees(
        self,
        batch: OrganizationSyncBatch,
        stats: dict[str, int],
    ) -> None:
        """把远端已不存在的本地员工置为离职/停用。

        前置条件（缺一不可）：
        1. 全量模式；
        2. 本次快照完整（所有部门都成功拉到）。

        增量模式下「本次没拉到」不能等同于「已离职」，因为增量本身
        就只覆盖部分部门。
        """
        if self.mode != "full":
            return
        if not self._snapshot_is_complete():
            logger.warning("快照不完整，跳过离职判定（避免误停用）")
            return

        remote_employee_ids = {
            str(item.get("user_id"))
            for item in self._current_employees
            if item.get("user_id")
        }
        local_rows = self.db.execute(
            select(Employee).where(
                Employee.feishu_user_id.is_not(None),
                Employee.feishu_deleted_at.is_(None),
            )
        ).scalars().all()

        for employee in local_rows:
            if employee.id in remote_employee_ids:
                continue
            if employee.feishu_user_id and employee.feishu_user_id in remote_employee_ids:
                continue
            # 远端已无此人：置为离职并禁用账号。
            employee.employment_status = "exited"
            employee.feishu_deleted_at = _now()
            employee.last_seen_batch_id = batch.id
            if employee.account_status != "disabled":
                employee.token_version = (employee.token_version or 1) + 1
                employee.account_status = "disabled"
            stats["employees_synced"] += 1
        self.db.flush()

    def _upsert_employee(
        self,
        batch: OrganizationSyncBatch,
        item: dict[str, Any],
        defer_manager: bool = False,
    ) -> bool:
        user_id = str(item["user_id"])
        employee = self.db.execute(
            select(Employee).where(
                (Employee.id == user_id) | (Employee.feishu_user_id == user_id)
            )
        ).scalars().first()
        if employee is None:
            employee = Employee(
                id=user_id,
                name=str(item.get("name") or "Unknown"),
            )
            self.db.add(employee)
            employee.account_status = employee.account_status or "enabled"
            employee.token_version = employee.token_version or 1
        else:
            self.db.flush()

        status = item.get("status") or {}
        if item.get("invalid"):
            raise ValueError("模拟员工数据校验失败")
        employment_status = _resolve_employment_status(status)
        department_ids = [
            str(value) for value in (item.get("department_ids") or []) if value
        ]
        primary_department_id = department_ids[0] if department_ids else None
        manager_id = str(item.get("leader_user_id")) if item.get("leader_user_id") else None

        new_values = {
            "name": str(item.get("name") or employee.name or "Unknown"),
            "email": str(item.get("email")) if item.get("email") else None,
            "feishu_user_id": user_id,
            "feishu_open_id": str(item.get("open_id")) if item.get("open_id") else None,
            "feishu_union_id": str(item.get("union_id")) if item.get("union_id") else None,
            "department_id": primary_department_id,
            "position": str(item.get("job_title")) if item.get("job_title") else None,
            "manager_id": manager_id,
            "employment_status": employment_status,
            "feishu_deleted_at": None,
        }
        if defer_manager:
            # manager_id 自引用外键要求上级员工已存在，
            # 第一轮只比较不写入，第二轮统一回填。
            # 变化检测仍需包含 manager_id，否则上级变更会被漏报。
            manager_changed = employee.manager_id != manager_id
            new_values.pop("manager_id", None)
        else:
            manager_changed = False
        changed = (
            any(getattr(employee, key) != value for key, value in new_values.items())
            or manager_changed
        )
        # ⚠️ 先记住**旧**的在职状态 —— 下面的账号启停逻辑要靠它区分
        #    「离职后复职」（不自动恢复）与「冻结后解冻」（自动恢复）。
        #    在赋值之后再读 employee.employment_status 就只剩新值了。
        previous_employment_status = employee.employment_status
        for key, value in new_values.items():
            setattr(employee, key, value)

        if employment_status == "exited":
            if employee.account_status != "disabled":
                changed = True
                employee.token_version += 1
            employee.account_status = "disabled"
        elif employment_status == "inactive":
            # 冻结 / 未激活 —— 账号禁用，但**保留可恢复的语义**。
            # ⚠️ 与离职的区别：离职是永久的（要人工复职），
            #    冻结是临时的（解冻后应自动恢复登录）。
            #    token_version 递增 → 已签发的 token 立即失效，
            #    冻结期间无法继续用旧 token 访问。
            if employee.account_status != "disabled":
                changed = True
                employee.token_version += 1
            employee.account_status = "disabled"
        elif employment_status == "active" and employee.account_status == "disabled":
            # 解冻 / 复职 → **自动恢复**账号。
            # ⚠️ 离职后复职仍需人工确认绑定，不自动恢复本地账号。
            #    靠**旧**状态区分：旧状态是 exited → 人工处理；
            #    旧状态是 inactive（冻结）→ 自动恢复。
            if previous_employment_status != "exited":
                changed = True
                employee.token_version += 1
                employee.account_status = "enabled"

        self.db.flush()
        self._sync_employee_departments(employee.id, department_ids)
        self._sync_identities(employee.id, item, batch)
        return changed

    def _sync_employee_departments(
        self, employee_id: str, department_ids: list[str]
    ) -> None:
        existing = {
            row.department_id: row
            for row in self.db.execute(
                select(EmployeeDepartment).where(
                    EmployeeDepartment.employee_id == employee_id
                )
            ).scalars().all()
        }
        wanted = set(department_ids)
        for department_id in wanted - set(existing):
            self.db.add(
                EmployeeDepartment(
                    employee_id=employee_id,
                    department_id=department_id,
                    is_primary=department_id == (department_ids[0] if department_ids else None),
                )
            )
        for department_id, row in existing.items():
            if department_id not in wanted:
                self.db.delete(row)
            else:
                row.is_primary = department_id == (department_ids[0] if department_ids else None)
        self.db.flush()

    def _sync_identities(
        self,
        employee_id: str,
        item: dict[str, Any],
        batch: OrganizationSyncBatch,
    ) -> None:
        values = {
            "user_id": str(item.get("user_id") or ""),
            "open_id": str(item.get("open_id") or ""),
            "union_id": str(item.get("union_id") or ""),
        }
        for identity_type, identity_value in values.items():
            if not identity_value:
                continue
            row = self.db.execute(
                select(FeishuIdentity).where(
                    FeishuIdentity.identity_type == identity_type,
                    FeishuIdentity.identity_value == identity_value,
                )
            ).scalar_one_or_none()
            if row and row.employee_id != employee_id and row.active:
                raise IntegrityError(
                    "statement",
                    {},
                    Exception(
                        f"飞书身份 {identity_type} 已绑定到其他员工，禁止静默换绑"
                    ),
                )
            if row is None:
                self.db.add(
                    FeishuIdentity(
                        employee_id=employee_id,
                        identity_type=identity_type,
                        identity_value=identity_value,
                        active=True,
                        bound_by=self.triggered_by,
                        bound_at=_now(),
                    )
                )
            elif not row.active:
                row.employee_id = employee_id
                row.active = True
                row.unbound_at = None
                row.bound_by = self.triggered_by
                row.bound_at = _now()
        self.db.flush()

    # ─────────────────────────────────────────────────────────────
    # 对账与数据质量
    # ─────────────────────────────────────────────────────────────

    def _reconcile(
        self,
        departments: list[dict[str, Any]],
        employees: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """对账：比较飞书与本地数量，并输出数据质量指标。"""
        remote_department_ids = {str(item.get("department_id")) for item in departments}
        remote_employee_ids = {str(item.get("user_id")) for item in employees}
        local_department_ids = set(
            self.db.execute(
                select(Department.id).where(Department.feishu_deleted_at.is_(None))
            ).scalars().all()
        )
        local_employee_ids = set(
            self.db.execute(
                select(Employee.id).where(Employee.feishu_deleted_at.is_(None))
            ).scalars().all()
        )
        local_active_employee_ids = set(
            self.db.execute(
                select(Employee.id).where(
                    Employee.employment_status == "active",
                    Employee.feishu_deleted_at.is_(None),
                )
            ).scalars().all()
        )
        remote_active_employee_ids = {
            str(item.get("user_id"))
            for item in employees
            if (item.get("status") or {}).get("is_exited") is not True
            and (item.get("status") or {}).get("is_activated", True)
        }
        unbound = self.identity_service.list_unbound()
        conflicts = self.identity_service.detect_conflicts()
        quality = self._data_quality(remote_employee_ids)
        return {
            "remote_departments": len(remote_department_ids),
            "local_departments": len(local_department_ids),
            "missing_local_departments": sorted(remote_department_ids - local_department_ids),
            "stale_local_departments": sorted(local_department_ids - remote_department_ids),
            "remote_employees": len(remote_employee_ids),
            "local_employees": len(local_employee_ids),
            "missing_local_employees": sorted(remote_employee_ids - local_employee_ids),
            "stale_local_employees": sorted(local_employee_ids - remote_employee_ids),
            "active_remote_employees": len(remote_active_employee_ids),
            "active_local_employees": len(local_active_employee_ids),
            "active_count_mismatch": len(remote_active_employee_ids) != len(local_active_employee_ids),
            "unbound_count": len(unbound),
            "conflict_count": len(conflicts),
            "conflicts": conflicts,
            "snapshot_complete": self._snapshot_is_complete(),
            "failed_departments": list(self._failed_departments),
            "data_quality": quality,
        }

    def _data_quality(self, remote_employee_ids: set[str]) -> dict[str, Any]:
        """数据质量指标：供前端展示「组织架构健康度」。

        这些指标是正式把飞书作为组织架构事实来源前的准入条件。
        """
        rows = self.db.execute(
            select(Employee).where(Employee.feishu_deleted_at.is_(None))
        ).scalars().all()

        no_department = [e.id for e in rows if not e.department_id]
        no_manager = [
            e.id
            for e in rows
            if e.employment_status == "active" and not e.manager_id
        ]
        no_login_account = [
            e.id
            for e in rows
            if e.employment_status == "active" and not e.login_account
        ]
        disabled = [e.id for e in rows if e.account_status == "disabled"]
        exited = [e.id for e in rows if e.employment_status == "exited"]

        # 循环上级关系：沿 manager_id 上溯，命中已访问节点即为环。
        manager_map = {e.id: e.manager_id for e in rows}
        cyclic: list[str] = []
        for employee_id in manager_map:
            seen: set[str] = set()
            current: str | None = employee_id
            while current and current in manager_map:
                if current in seen:
                    cyclic.append(employee_id)
                    break
                seen.add(current)
                current = manager_map.get(current)

        return {
            "total": len(rows),
            "no_department_count": len(no_department),
            "no_department_ids": sorted(no_department)[:100],
            "no_manager_count": len(no_manager),
            "no_manager_ids": sorted(no_manager)[:100],
            "no_login_account_count": len(no_login_account),
            "no_login_account_ids": sorted(no_login_account)[:100],
            "disabled_count": len(disabled),
            "exited_count": len(exited),
            "cyclic_manager_count": len(set(cyclic)),
            "cyclic_manager_ids": sorted(set(cyclic))[:100],
            "remote_synced_count": len(remote_employee_ids),
        }

    # ─────────────────────────────────────────────────────────────
    # 批次自愈与重试
    # ─────────────────────────────────────────────────────────────

    def recover_stale_batches(self, stale_after_minutes: int = 30) -> int:
        """把超时的 running 批次标记为 failed，返回恢复数量。

        多实例部署时，若某实例在同步过程中被 kill，批次会永久停留在
        running 状态。每次调度前调用本方法做自愈。
        """
        threshold = _now() - timedelta(minutes=stale_after_minutes)
        stale = self.db.execute(
            select(OrganizationSyncBatch).where(
                OrganizationSyncBatch.status == "running",
                OrganizationSyncBatch.started_at < threshold,
            )
        ).scalars().all()
        for batch in stale:
            batch.status = "failed"
            batch.error_message = "同步超时未完成（实例异常退出或重启），请手工重试"
            batch.phase = "finished"
            batch.finished_at = _now()
        if stale:
            self.db.commit()
        return len(stale)

    def retry_failed(self, *, actor_id: str | None = None) -> dict[str, Any]:
        """重试失败记录。

        语义：
        - 只重试「到期」的记录（``next_retry_at`` 已到）；
        - 可自动重试的分类（``retryable``）失败后按指数退避安排下次重试；
        - 其它分类（配置 / 人工 / 永久错误）不再自动重试，转 ``dead_letter``
          等待人工处理；
        - 重试次数超过上限的记录进入 ``dead_letter``。
        """
        errors = self.db.execute(
            select(OrganizationSyncItemError)
            .where(
                OrganizationSyncItemError.status.in_(("pending", "retrying")),
                (OrganizationSyncItemError.next_retry_at.is_(None))
                | (OrganizationSyncItemError.next_retry_at <= _now()),
            )
            .order_by(OrganizationSyncItemError.created_at)
        ).scalars().all()
        retried = 0
        resolved = 0
        failed = 0
        dead_lettered = 0
        for error in errors:
            error.status = "retrying"
            error.retry_count += 1
            self.db.commit()
            savepoint = None
            try:
                # 每条重试独立 SAVEPOINT：单条失败只回滚这条，
                # 不影响其它已重试成功的记录（与主同步流程同一原则）。
                savepoint = self._savepoint()
                if error.resource_type == "employee" and error.resource_id:
                    # 优先取飞书最新数据。
                    #
                    # 回落规则：**只有**在「该用户已不可取」这类不可重试的
                    # 失败（404 / 员工已删除）时，才回落到失败记录里保存的
                    # payload —— 补偿重试不能因为一次拉取失败就彻底放弃，
                    # 否则历史失败记录永远清不掉。
                    #
                    # 限流 / 网络抖动等**可重试**失败必须继续向上抛，
                    # 让退避逻辑接管；否则会被误判成「数据有问题」而进死信。
                    payload: dict[str, Any] = {}
                    try:
                        fetched = self.feishu_client.fetch_employee(error.resource_id)
                    except FeishuError as fetch_exc:
                        if fetch_exc.retryable:
                            raise
                        logger.warning(
                            "重试时拉取飞书员工失败，回落使用已保存 payload, user_id=%s, 原因=%s",
                            error.resource_id,
                            self._safe_error(fetch_exc),
                        )
                        fetched = None
                    payload = fetched or (error.payload_json or {})
                    self._retry_employee(payload, error)
                elif error.resource_type == "department" and error.resource_id:
                    # 部门详情接口按实际飞书版本补充；失败仍保留待补偿记录。
                    payload = error.payload_json or {}
                    self._retry_department(payload, error)
                else:
                    raise ValueError("不支持自动补偿的资源类型")
                savepoint.commit()
                error.status = "resolved"
                error.resolved_at = _now()
                resolved += 1
            except Exception as exc:
                self._rollback_savepoint(savepoint)
                safe_message = self._safe_error(exc)
                error.message = safe_message
                error.error_category = classify_feishu_error(exc)
                failed += 1

                if error.retry_count >= MAX_RETRY_COUNT:
                    # 超过重试上限：进入死信，等待人工处理。
                    error.status = "dead_letter"
                    error.next_retry_at = None
                    dead_lettered += 1
                elif is_auto_retryable(error.error_category or ""):
                    # 可自动重试：按退避安排下次重试。
                    error.status = "pending"
                    delay = _retry_backoff_seconds(error.retry_count)
                    error.next_retry_at = _now() + timedelta(seconds=delay)
                else:
                    # 配置 / 人工 / 永久错误：重试无意义，等待人工。
                    error.status = "dead_letter"
                    error.next_retry_at = None
                    dead_lettered += 1
            retried += 1
            self.db.commit()
        self._audit(
            None,
            action="retry_failed_items",
            result="ok" if failed == 0 else "partial_failed",
            detail={
                "retried": retried,
                "resolved": resolved,
                "failed": failed,
                "dead_lettered": dead_lettered,
                "actor_id": actor_id,
            },
        )
        self.db.commit()
        return {
            "retried": retried,
            "resolved": resolved,
            "failed": failed,
            "dead_lettered": dead_lettered,
        }

    def _retry_employee(self, payload: dict[str, Any], error: OrganizationSyncItemError) -> None:
        batch = self.db.get(OrganizationSyncBatch, error.batch_id)
        if batch is None:
            raise ValueError("原同步批次不存在")
        # 重试路径同样要延迟写 manager_id：上级员工可能还没落库，
        # 直接写会触发 employee.id 自引用外键失败。
        # manager_id 的回填由调用方在全部重试完成后统一补。
        self._upsert_employee(batch, payload, defer_manager=True)

    def _retry_department(self, payload: dict[str, Any], error: OrganizationSyncItemError) -> None:
        batch = self.db.get(OrganizationSyncBatch, error.batch_id)
        if batch is None:
            raise ValueError("原同步批次不存在")
        self._upsert_department(batch, payload)

    # ─────────────────────────────────────────────────────────────
    # 失败记录与审计
    # ─────────────────────────────────────────────────────────────

    def _record_error(
        self,
        batch: OrganizationSyncBatch,
        resource_type: str,
        resource_id: str | None,
        message: str,
        payload: dict[str, Any] | None = None,
        *,
        commit: bool = True,
    ) -> None:
        """记录单条失败，并按错误分级决定是否允许自动重试。

        分级决定后续行为：
        - ``retryable``：进入自动重试队列，按退避时间重试；
        - 其它分级：等待人工处理（``pending``），不自动重试。

        ``commit=False`` 用于 SAVEPOINT 场景：失败记录必须留在**外层事务**里
        一起提交。若在这里单独 commit，会把外层事务（尚未提交的部门写入）
        提前提交掉，破坏批次的事务边界。
        """
        category = classify_item_failure(message)
        auto_retryable = is_auto_retryable(category)
        self.db.add(
            OrganizationSyncItemError(
                batch_id=batch.id,
                resource_type=resource_type,
                resource_id=resource_id,
                message=message,
                error_code="SYNC_ITEM_FAILED",
                error_category=category,
                status="pending",
                # 可自动重试的失败安排退避时间；其它失败立即等待人工。
                next_retry_at=(
                    _now() + timedelta(seconds=RETRY_BACKOFF_SECONDS[0])
                    if auto_retryable
                    else None
                ),
                payload_json=self._sanitize_payload(payload),
            )
        )
        if commit:
            self.db.commit()

    def _audit(
        self,
        batch: OrganizationSyncBatch | None,
        *,
        action: str,
        result: str,
        detail: dict[str, Any],
    ) -> None:
        self.db.add(
            OrganizationSyncAudit(
                batch_id=batch.id if batch else None,
                actor_id=self.triggered_by,
                action=action,
                resource_type="organization",
                result=result,
                detail_json=detail,
            )
        )

    @staticmethod
    def _safe_error(exc: Exception) -> str:
        """错误信息不得包含 token、secret、Authorization。"""
        message = str(exc)
        forbidden = ("app_secret", "tenant_access_token", "authorization", "bearer ")
        for item in forbidden:
            if item in message.lower():
                return "飞书接口错误（敏感信息已隐藏）"
        return message[:1000]

    @staticmethod
    def _sanitize_payload(payload: dict[str, Any] | None) -> dict[str, Any] | None:
        if payload is None:
            return None
        sensitive = {"access_token", "tenant_access_token", "app_secret", "authorization"}
        return {
            key: ("***" if key.lower() in sensitive else value)
            for key, value in payload.items()
            if key.lower() not in sensitive
        }
