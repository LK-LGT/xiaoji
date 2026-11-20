"""飞书方案来源绑定：自动发现、人工覆盖与变更检测。

三条能力
--------
1. **人工绑定**：HR 在系统里填「部门 + 飞书文档链接」，立刻建立绑定。
   这是**优先级最高**的来源 —— 自动发现永远不会覆盖它。

2. **自动发现**：扫描配置的 wiki 空间，按命名规则（如《XX部门绩效方案》）
   匹配部门，自动建立绑定。空间扫描需要 ``wiki:wiki:readonly`` 权限，
   权限未开通时自动降级为「跳过发现」，不影响已有绑定。

3. **变更检测**：对比飞书文档的 ``obj_edit_time``，判断是否被改过。
   这个能力只依赖 ``wiki`` 节点读权限，**实测可用**，所以
   「文档变了没有」这件事永远能回答。

⚠️ 权限现实（2026-11-20 实测）
------------------------------
    ✅ get_node            → 变更检测可用
    ❌ spaces / nodes 列表 → 缺 wiki:wiki:readonly ⇒ 自动发现不可用
    ❌ docx / drive / sheets → 缺对应 scope ⇒ 正文取不到

⇒ 所以本模块**不假设**任何权限存在，每个能力都真实试一次再降级。
"""

from __future__ import annotations

import logging
import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domain.feishu_scheme_models import FeishuSchemeSource
from ..domain.models import Department, _now
from .feishu_client import FeishuError
from .feishu_docs import (
    DocsContentUnavailable,
    DocsPermissionError,
    FeishuDocsClient,
    is_scope_denied,
)

logger = logging.getLogger(__name__)

#: 绑定模式
MODE_AUTO = "auto"
MODE_MANUAL = "manual"

#: 飞书链接里取 wiki token 的形态：
#:   /wiki/{token}          → 知识库节点
#:   /docx/{token}          → 云文档
#:   /sheets/{token}        → 电子表格
#:   /docs/{token}          → 旧版云文档
_WIKI_URL_PATTERN = re.compile(
    r"/(?:wiki|docx|sheets|docs|file)/([A-Za-z0-9]+)", re.IGNORECASE
)

#: 方案文档标题里应包含的关键词（用于自动发现时的识别）
SCHEME_TITLE_KEYWORDS = ("绩效", "考核", "方案", "考评")


def extract_token(source_url: str) -> str:
    """从飞书链接里提取 token。

    支持：
    - wiki 链接  ``https://xxx.feishu.cn/wiki/{token}``
    - docx 链接 ``https://xxx.feishu.cn/docx/{token}``
    - 直接给 token

    Returns:
        token；无法识别时返回空串。
    """
    if not source_url:
        return ""
    text = source_url.strip()
    match = _WIKI_URL_PATTERN.search(text)
    if match:
        return match.group(1)
    # 裸 token（只允许字母数字，长度合理）
    if re.fullmatch(r"[A-Za-z0-9]{8,64}", text):
        return text
    return ""


def match_department(title: str, departments: list[Department]) -> Department | None:
    """按标题猜部门。

    ⚠️ **必须唯一命中**才返回 —— 猜错就是把 A 部门的方案套到 B 部门，
    比匹配不上严重得多。多个部门都命中时返回 ``None``。
    """
    if not title:
        return None
    # 去掉《》与常见后缀，避免「销售部绩效方案」里的「部」干扰
    cleaned = title.replace("《", "").replace("》", "").replace("【", "").replace("】", "")
    hits = []
    for dept in departments:
        name = dept.name or ""
        if not name:
            continue
        if name in cleaned:
            hits.append(dept)
    if len(hits) == 1:
        return hits[0]
    # ⚠️ **命中多个就直接拒绝** —— 不再往下试「去后缀」匹配。
    # 「短视频」与「短视频二部」同时命中时，去后缀匹配会让
    # 「短视频二部」去掉「部」后也命中，从而**错误地返回唯一结果** ——
    # 那比直接拒绝危险得多（把 A 部门方案套到 B 部门）。
    if len(hits) > 1:
        return None
    # 没精确命中时，再试一次：去掉部门名末尾的「部」「中心」「组」
    trimmed_hits = []
    for dept in departments:
        name = (dept.name or "").rstrip()
        for suffix in ("部", "中心", "组", "科"):
            if name.endswith(suffix) and len(name) > len(suffix):
                if name[: -len(suffix)] in cleaned:
                    trimmed_hits.append(dept)
                break
    unique = {d.id: d for d in trimmed_hits}
    if len(unique) == 1:
        return next(iter(unique.values()))
    return None


def looks_like_scheme(title: str) -> bool:
    """标题是否像绩效方案文档。"""
    if not title:
        return False
    return any(keyword in title for keyword in SCHEME_TITLE_KEYWORDS)


def source_code(department: Department, period_type: str = "monthly") -> str:
    """生成方案编码。

    规则：``SCHEME_{部门ID}_{周期类型}`` —— 部门 ID 全局唯一，
    所以编码也唯一，且可读。
    """
    return f"SCHEME_{department.id}_{period_type}".upper()[:64]


class SchemeSourceService:
    """飞书方案来源绑定的读写与发现。"""

    def __init__(self, db: Session, docs: FeishuDocsClient) -> None:
        self.db = db
        self.docs = docs

    # ─────────────────────────────────────────────────────────────
    # 查询
    # ─────────────────────────────────────────────────────────────

    def list_sources(self, *, only_active: bool = False) -> list[FeishuSchemeSource]:
        stmt = select(FeishuSchemeSource).order_by(
            FeishuSchemeSource.department_id, FeishuSchemeSource.created_at
        )
        if only_active:
            stmt = stmt.where(FeishuSchemeSource.active.is_(True))
        return list(self.db.scalars(stmt).all())

    def get(self, source_id: str) -> FeishuSchemeSource | None:
        return self.db.get(FeishuSchemeSource, source_id)

    def get_by_department(
        self, department_id: str, period_type: str = "monthly"
    ) -> FeishuSchemeSource | None:
        return self.db.scalar(
            select(FeishuSchemeSource).where(
                FeishuSchemeSource.department_id == department_id,
                FeishuSchemeSource.period_type == period_type,
            )
        )

    # ─────────────────────────────────────────────────────────────
    # 人工绑定
    # ─────────────────────────────────────────────────────────────

    def bind_manual(
        self,
        *,
        department_id: str,
        source_url: str,
        period_type: str = "monthly",
        position: str = "",
        scheme_code: str = "",
        actor_id: str | None = None,
    ) -> FeishuSchemeSource:
        """人工绑定一份飞书方案文档。

        ⚠️ **人工绑定优先**：若该部门已有**自动发现**的绑定，
        这里直接**升级**为人工绑定（保留原 obj_token，刷新元信息）；
        若已有**人工**绑定，则更新其元信息。
        """
        token = extract_token(source_url)
        if not token:
            raise ValueError("无法识别飞书文档链接，请粘贴形如 /wiki/xxxx 的完整链接")

        dept = self.db.get(Department, department_id)
        if dept is None or not dept.active:
            raise ValueError("部门不存在或已停用")

        node = self.docs.get_node(token)
        if not node.get("obj_token"):
            raise ValueError("未能从该链接解析出飞书文档，请确认链接指向具体文档")

        existing = self.get_by_department(department_id, period_type)
        if existing is None:
            existing = self.db.scalar(
                select(FeishuSchemeSource).where(
                    FeishuSchemeSource.obj_token == node["obj_token"]
                )
            )
        if existing is None:
            existing = FeishuSchemeSource(
                obj_token=node["obj_token"],
                department_id=department_id,
                period_type=period_type,
            )
            self.db.add(existing)

        existing.node_token = node.get("node_token") or token
        existing.obj_token = node["obj_token"]
        existing.obj_type = node.get("obj_type") or "docx"
        existing.title = node.get("title") or ""
        existing.space_id = node.get("space_id") or ""
        existing.source_url = source_url
        existing.department_id = department_id
        existing.period_type = period_type
        existing.position = position
        existing.scheme_code = scheme_code or source_code(dept, period_type)
        existing.binding_mode = MODE_MANUAL
        existing.active = True
        self.db.flush()

        from ..domain.models import AuditLog

        self.db.add(
            AuditLog(
                actor_id=actor_id,
                action="feishu_scheme_source.bind_manual",
                resource_type="feishu_scheme_source",
                resource_id=existing.id,
                metadata_json={
                    "department_id": department_id,
                    "obj_token": existing.obj_token,
                },
            )
        )
        return existing

    def unbind(self, source_id: str, *, actor_id: str | None = None) -> None:
        """解除绑定（停用，不物理删除 —— 保留审计线索）。"""
        source = self.get(source_id)
        if source is None:
            raise ValueError("绑定不存在")
        source.active = False

        from ..domain.models import AuditLog

        self.db.add(
            AuditLog(
                actor_id=actor_id,
                action="feishu_scheme_source.unbind",
                resource_type="feishu_scheme_source",
                resource_id=source_id,
            )
        )
        self.db.flush()

    def set_active(self, source_id: str, active: bool) -> FeishuSchemeSource:
        """启用 / 停用绑定。"""
        source = self.get(source_id)
        if source is None:
            raise ValueError("绑定不存在")
        source.active = active
        self.db.flush()
        return source

    # ─────────────────────────────────────────────────────────────
    # 自动发现
    # ─────────────────────────────────────────────────────────────

    def discover(self, space_id: str = "", *, dry_run: bool = False) -> dict[str, Any]:
        """扫描 wiki 空间，自动建立「部门 ↔ 方案文档」绑定。

        ⚠️ **权限不足时优雅降级**：空间节点列表需要 ``wiki:wiki:readonly``。
        2026-11-20 实测本应用**没有**该权限 ⇒ 返回 ``skipped``，
        不抛异常、不影响已有绑定 —— 人工绑定照常可用。

        Args:
            space_id: 要扫描的空间；留空则用配置里的默认空间。
            dry_run: 只报告不落库。

        Returns:
            ``{"status": "ok"|"skipped"|"failed", "discovered": [...], ...}``
        """
        from ..core.config import settings

        target_space = space_id or settings.feishu_scheme_sync_space_id
        if not target_space:
            return {
                "status": "skipped",
                "reason": "未配置飞书知识空间（FEISHU_SCHEME_SYNC_SPACE_ID）",
                "discovered": [],
            }

        try:
            nodes = self._list_space_nodes(target_space)
        except DocsPermissionError as exc:
            logger.warning("自动发现跳过：缺少 wiki 空间读权限（%s）", exc.message)
            return {
                "status": "skipped",
                "reason": f"缺少 wiki 空间读权限：{exc.message}",
                "discovered": [],
            }
        except DocsContentUnavailable as exc:
            return {
                "status": "failed",
                "reason": f"读取飞书知识空间失败：{exc.message}",
                "discovered": [],
            }

        departments = list(
            self.db.scalars(select(Department).where(Department.active.is_(True))).all()
        )
        discovered: list[dict[str, Any]] = []
        for node in nodes:
            title = str(node.get("title") or "")
            if not looks_like_scheme(title):
                continue
            dept = match_department(title, departments)
            if dept is None:
                continue
            entry = {
                "title": title,
                "obj_token": str(node.get("obj_token") or ""),
                "obj_type": str(node.get("obj_type") or "docx"),
                "node_token": str(node.get("node_token") or ""),
                "department_id": dept.id,
                "department_name": dept.name,
            }
            discovered.append(entry)

            if dry_run:
                continue

            # ⚠️ **人工绑定优先** —— 自动发现只填空位，不覆盖人工绑定。
            existing = self.get_by_department(dept.id, settings.feishu_scheme_sync_period_type)
            if existing is not None:
                continue
            if not node.get("obj_token"):
                continue
            self.db.add(
                FeishuSchemeSource(
                    node_token=str(node.get("node_token") or ""),
                    obj_token=str(node["obj_token"]),
                    obj_type=str(node.get("obj_type") or "docx"),
                    title=title,
                    space_id=target_space,
                    department_id=dept.id,
                    period_type=settings.feishu_scheme_sync_period_type,
                    scheme_code=source_code(dept, settings.feishu_scheme_sync_period_type),
                    binding_mode=MODE_AUTO,
                    active=True,
                )
            )
        if not dry_run:
            self.db.flush()
        return {
            "status": "ok",
            "space_id": target_space,
            "scanned": len(nodes),
            "discovered": discovered,
        }

    def _list_space_nodes(self, space_id: str) -> list[dict[str, Any]]:
        """列出空间下的节点（分页）。

        ⚠️ **两种「没权限」要分开说**
        - ``99991672`` → 应用**没申请** ``wiki:wiki:readonly`` scope
        - ``131006``   → scope 有，但**应用没被加入这个知识空间**
          （需要在知识库设置里把应用加为成员）

        后者更常见 —— scope 批了不等于能读任意空间。
        """
        nodes: list[dict[str, Any]] = []
        page_token = ""
        for _ in range(50):
            params: dict[str, Any] = {"page_size": 50}
            if page_token:
                params["page_token"] = page_token
            try:
                data = self.docs.client._request(
                    "GET", f"/wiki/v2/spaces/{space_id}/nodes", params=params
                )
            except FeishuError as exc:
                if exc.code == 131006:
                    raise DocsPermissionError(
                        "应用未被加入该飞书知识空间（需在知识库设置里把应用加为成员），"
                        f"空间 {space_id}",
                        code=exc.code,
                    ) from exc
                if is_scope_denied(exc):
                    raise DocsPermissionError(
                        f"缺少 wiki 空间读权限：{exc.message}", code=exc.code
                    ) from exc
                raise DocsContentUnavailable(
                    f"读取飞书知识空间失败：{exc.message}"
                ) from exc
            nodes.extend(data.get("items") or [])
            page_token = str(data.get("page_token") or "")
            if not page_token:
                break
        return nodes

    # ─────────────────────────────────────────────────────────────
    # 变更检测
    # ─────────────────────────────────────────────────────────────

    def check_changed(self, source: FeishuSchemeSource) -> bool:
        """判断文档是否被改过。

        ⚠️ **只依赖 ``wiki`` 节点读权限** —— 实测可用。
        所以即使正文权限没开通，也**能回答「文档变了没有」**。

        ⚠️ **取不到编辑时间时保守返回 False** —— 不能因为一次网络抖动
        就判定「有变更」而触发一轮同步。
        """
        token = source.node_token or source.obj_token
        if not token:
            return False
        try:
            node = self.docs.get_node(token)
        except (DocsPermissionError, DocsContentUnavailable) as exc:
            logger.warning(
                "变更检测失败（source=%s, obj=%s）：%s", source.id, source.obj_token, exc.message
            )
            return False
        edit_time = str(node.get("edit_time") or "")
        if not edit_time or not source.last_edit_time:
            # 首次同步：没有基线，视为「需要同步」
            return True
        return edit_time != source.last_edit_time

    def refresh_metadata(self, source: FeishuSchemeSource) -> dict[str, Any]:
        """刷新来源的元信息（标题、编辑时间）。"""
        token = source.node_token or source.obj_token
        if not token:
            return {}
        try:
            node = self.docs.get_node(token)
        except (DocsPermissionError, DocsContentUnavailable):
            return {}
        if node.get("title"):
            source.title = node["title"]
        if node.get("obj_token"):
            source.obj_token = node["obj_token"]
        if node.get("obj_type"):
            source.obj_type = node["obj_type"]
        if node.get("edit_time"):
            source.last_edit_time = node["edit_time"]
        source.updated_at = _now()
        self.db.flush()
        return node


__all__ = [
    "MODE_AUTO",
    "MODE_MANUAL",
    "SchemeSourceService",
    "extract_token",
    "looks_like_scheme",
    "match_department",
    "source_code",
]
