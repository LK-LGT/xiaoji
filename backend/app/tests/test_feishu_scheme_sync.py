"""飞书绩效方案自动同步 —— 验收测试。

覆盖
----
1. 链接解析（wiki / docx / 裸 token）
2. 部门匹配（唯一命中才认，多命中拒绝）
3. 变更检测（依赖 obj_edit_time，**不需要正文权限**）
4. 人工绑定优先于自动发现
5. 权限缺失时**优雅降级**（不抛异常、不静默失败）
6. 生效期顺延（新版本接在旧版本后，**旧版本不动**）
7. 内容指纹幂等（同一内容不重复建版本）
8. 缺等级规则时**只建草稿**（不自动发布）
9. 自动发起考核的幂等

⚠️ **本文件不联网** —— 飞书交互全部用假客户端模拟。
"""

from __future__ import annotations

import io
import zipfile
from datetime import date, timedelta

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app.domain.feishu_scheme_models import (
    FeishuSchemeSource,
    FeishuSchemeSyncBatch,
)
from backend.app.domain.models import (
    Base,
    Department,
    Employee,
    HrDepartmentConfig,
)
from backend.app.domain.scheme_models import SchemeBinding, SchemeVersion
from backend.app.services import scheme_service
from backend.app.services.feishu_docs import (
    DocsContentUnavailable,
    DocsPermissionError,
    FeishuDocsClient,
)
from backend.app.services.feishu_scheme_source import (
    MODE_AUTO,
    MODE_MANUAL,
    SchemeSourceService,
    extract_token,
    looks_like_scheme,
    match_department,
    source_code,
)
from backend.app.services.feishu_scheme_sync import (
    RESULT_FAILED,
    RESULT_SKIPPED,
    RESULT_SYNCED,
    RESULT_UNCHANGED,
    FeishuSchemeSyncService,
    next_effective_window,
)

# ─────────────────────────────────────────────────────────────
# 假客户端
# ─────────────────────────────────────────────────────────────


class FakeDocsClient(FeishuDocsClient):
    """假的云文档客户端。

    ``nodes``   —— token → 节点元信息
    ``content`` —— obj_token → (bytes, filename)
    ``denied``  —— 这些 obj_token 一律报「缺权限」
    """

    def __init__(self, nodes=None, content=None, denied=None, edit_times=None):
        self.nodes = nodes or {}
        self.content = content or {}
        self.denied = set(denied or ())
        self.edit_times = edit_times or {}
        self.client = None

    def get_node(self, node_token):
        if node_token in self.denied:
            raise DocsPermissionError("缺少 wiki 权限", code=99991672)
        node = self.nodes.get(node_token)
        if node is None:
            raise DocsContentUnavailable("飞书文档节点不存在")
        result = dict(node)
        if node_token in self.edit_times:
            result["edit_time"] = self.edit_times[node_token]
        return result

    def fetch_document_bytes(self, obj_token, obj_type):
        if obj_token in self.denied:
            raise DocsPermissionError("缺少 docx 权限", code=99991672)
        item = self.content.get(obj_token)
        if item is None:
            raise DocsContentUnavailable("文档没有可解析的正文块")
        return item


@pytest.fixture
def db():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    session.add_all(
        [
            Department(id="HR", name="人事"),
            Department(id="D", name="短视频"),
            Department(id="D2", name="短视频二部"),
            Department(id="X", name="其他"),
        ]
    )
    session.flush()
    session.add_all(
        [
            Employee(id="H", name="HR", department_id="HR", position="HR"),
            Employee(id="E", name="员工", department_id="D", position="剪辑"),
        ]
    )
    session.add(HrDepartmentConfig(department_id="HR", active=True))
    session.commit()
    yield session
    session.close()
    engine.dispose()


# ─────────────────────────────────────────────────────────────
# 测试数据：一份可被 parse_document 识别的 docx
# ─────────────────────────────────────────────────────────────


def build_docx(with_grades: bool = True, changed: bool = False) -> bytes:
    """构造一份含「考核指标 / 分值 / 评分标准」表格的 docx。

    ``with_grades=True`` 时额外带一张「等级 / 总分区间」表 ——
    这是**自动发布**的必要条件（没有等级规则，主管无法判定等级）。

    ``changed=True`` 时改动分值 —— 用于模拟「文档内容真的变了」
    （内容不变但编辑时间变，指纹相同会被幂等跳过，不该建新版本）。
    """
    ns = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
    rows = [
        ("考核指标", "分值", "评分标准"),
        ("视频产出量", "50" if changed else "40", "每月不少于20条，按完成比例折算"),
        ("完播率", "30", "高于部门平均线得满分"),
        ("选题质量", "30", "主管依据原文评价"),
    ]
    cells = "".join(
        "<w:tr>"
        + "".join(
            f'<w:tc><w:p><w:r><w:t>{c}</w:t></w:r></w:p></w:tc>' for c in row
        )
        + "</w:tr>"
        for row in rows
    )
    tables = f"<w:tbl>{cells}</w:tbl>"
    if with_grades:
        grade_rows = [
            ("等级", "总分区间", "说明"),
            ("A", "90-100", "优秀"),
            ("B", "80-89", "良好"),
            ("C", "60-79", "合格"),
            ("D", "0-59", "不合格"),
        ]
        grade_cells = "".join(
            "<w:tr>"
            + "".join(
                f'<w:tc><w:p><w:r><w:t>{c}</w:t></w:r></w:p></w:tc>' for c in row
            )
            + "</w:tr>"
            for row in grade_rows
        )
        tables += f"<w:tbl>{grade_cells}</w:tbl>"
    document = (
        f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<w:document {ns}><w:body>'
        f"<w:p><w:r><w:t>短视频剪辑岗位绩效考核方案</w:t></w:r></w:p>"
        f"{tables}"
        f"</w:body></w:document>"
    )
    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
        "</Types>"
    )
    rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
        "</Relationships>"
    )
    doc_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"/>'
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("_rels/.rels", rels)
        archive.writestr("word/_rels/document.xml.rels", doc_rels)
        archive.writestr("word/document.xml", document)
    return buffer.getvalue()


NODE = {
    "node_token": "NODE1",
    "obj_token": "OBJ1",
    "obj_type": "docx",
    "title": "短视频剪辑岗位绩效考核方案",
    "space_id": "SPACE1",
    "edit_time": "1000",
}


def make_docs(**kwargs):
    kwargs.setdefault("nodes", {"NODE1": NODE})
    kwargs.setdefault("content", {"OBJ1": (build_docx(), "方案.docx")})
    return FakeDocsClient(**kwargs)


def make_source(db, docs, *, mode=MODE_MANUAL, active=True, **kwargs):
    source = FeishuSchemeSource(
        node_token="NODE1",
        obj_token="OBJ1",
        obj_type="docx",
        title="短视频剪辑岗位绩效考核方案",
        space_id="SPACE1",
        source_url="https://x.feishu.cn/wiki/NODE1",
        department_id="D",
        period_type="monthly",
        scheme_code=source_code(db.get(Department, "D")),
        binding_mode=mode,
        active=active,
        **kwargs,
    )
    db.add(source)
    db.commit()
    return source


# ─────────────────────────────────────────────────────────────
# 1. 链接解析
# ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://x.feishu.cn/wiki/Abc123?from=auth_notice", "Abc123"),
        ("https://x.feishu.cn/docx/Xyz789", "Xyz789"),
        ("https://x.feishu.cn/sheets/Sheet01", "Sheet01"),
        ("Abc12345", "Abc12345"),
        ("", ""),
        ("https://example.com/not-feishu", ""),
    ],
)
def test_extract_token(url, expected):
    assert extract_token(url) == expected


# ─────────────────────────────────────────────────────────────
# 2. 部门匹配
# ─────────────────────────────────────────────────────────────


def test_match_department_unique(db):
    depts = list(db.scalars(select(Department)).all())
    hit = match_department("短视频剪辑岗位绩效考核方案", depts)
    assert hit is not None and hit.id == "D"


def test_match_department_ambiguous_returns_none(db):
    """两个部门都命中时必须拒绝 —— 猜错比匹配不上严重得多。"""
    depts = list(db.scalars(select(Department)).all())
    assert match_department("短视频与短视频二部绩效方案", depts) is None


def test_looks_like_scheme():
    assert looks_like_scheme("XX部门绩效方案")
    assert not looks_like_scheme("周会纪要")


# ─────────────────────────────────────────────────────────────
# 3. 变更检测
# ─────────────────────────────────────────────────────────────


def test_change_detected_when_edit_time_changes(db):
    docs = make_docs(edit_times={"NODE1": "2000"})
    source = make_source(db, docs, last_edit_time="1000")
    service = SchemeSourceService(db, docs)
    assert service.check_changed(source) is True


def test_unchanged_when_edit_time_same(db):
    docs = make_docs(edit_times={"NODE1": "1000"})
    source = make_source(db, docs, last_edit_time="1000")
    service = SchemeSourceService(db, docs)
    assert service.check_changed(source) is False


def test_change_detection_survives_permission_error(db):
    """节点读不到时**保守返回 False** —— 不能因网络抖动就触发同步。"""
    docs = make_docs(denied={"NODE1"})
    source = make_source(db, docs, last_edit_time="1000")
    service = SchemeSourceService(db, docs)
    assert service.check_changed(source) is False


# ─────────────────────────────────────────────────────────────
# 4. 人工绑定优先
# ─────────────────────────────────────────────────────────────


def test_manual_binding_wins_over_auto(db):
    docs = make_docs()
    service = SchemeSourceService(db, docs)
    manual = service.bind_manual(
        department_id="D",
        source_url="https://x.feishu.cn/wiki/NODE1",
        actor_id="H",
    )
    db.commit()
    assert manual.binding_mode == MODE_MANUAL
    assert manual.department_id == "D"
    assert manual.scheme_code == "SCHEME_D_MONTHLY"

    # 自动发现不得覆盖人工绑定
    service._list_space_nodes = lambda space: [
        {
            "title": "短视频剪辑岗位绩效考核方案",
            "obj_token": "OBJ1",
            "obj_type": "docx",
            "node_token": "NODE1",
        }
    ]
    service.discover("SPACE1")
    db.commit()
    again = service.get_by_department("D", "monthly")
    assert again.binding_mode == MODE_MANUAL


def test_discover_skips_when_permission_denied(db):
    """空间读权限缺失时**返回 skipped 而不是抛异常**。"""
    docs = make_docs()
    service = SchemeSourceService(db, docs)

    def boom(*args, **kwargs):
        raise DocsPermissionError("缺少 wiki 权限", code=99991672)

    service._list_space_nodes = boom
    result = service.discover("SPACE1")
    assert result["status"] == "skipped"
    assert "权限" in result["reason"]


def test_discover_creates_auto_binding(db):
    """自动发现：标题命中部门且唯一 ⇒ 建立 auto 绑定。"""
    docs = make_docs()
    service = SchemeSourceService(db, docs)
    service._list_space_nodes = lambda space: [
        {
            "title": "短视频剪辑岗位绩效考核方案",
            "obj_token": "OBJ1",
            "obj_type": "docx",
            "node_token": "NODE1",
        }
    ]
    result = service.discover("SPACE1")
    db.commit()
    assert result["status"] == "ok"
    assert len(result["discovered"]) == 1
    source = service.get_by_department("D", "monthly")
    assert source is not None and source.binding_mode == MODE_AUTO


# ─────────────────────────────────────────────────────────────
# 5. 端到端同步
# ─────────────────────────────────────────────────────────────


def test_sync_creates_published_version(db):
    """文档变更 ⇒ 自动建版本 + 自动发布。"""
    docs = make_docs(edit_times={"NODE1": "2000"})
    make_source(db, docs, last_edit_time="1000")
    service = FeishuSchemeSyncService(db, docs, actor_id="H", trigger_type="manual")
    batch = service.run()
    db.commit()

    assert batch.status == "succeeded"
    assert batch.published_count == 1
    item = batch.detail["items"][0]
    assert item["result"] == RESULT_SYNCED
    assert item["metrics"] == 3

    version = db.scalar(select(SchemeVersion))
    assert version is not None
    assert version.status == "published"
    assert version.payload["code"] == "SCHEME_D_MONTHLY"
    assert len(version.payload["metrics"]) == 3
    binding = db.scalar(select(SchemeBinding))
    assert binding is not None
    assert binding.department_id == "D"


def test_sync_is_idempotent_on_same_content(db):
    """内容没变 ⇒ 不重复建版本。"""
    docs = make_docs(edit_times={"NODE1": "2000"})
    make_source(db, docs, last_edit_time="1000")
    service = FeishuSchemeSyncService(db, docs, actor_id="H")
    service.run()
    db.commit()
    assert db.query(SchemeVersion).count() == 1

    # 第二次：编辑时间变了但内容一样 ⇒ 不该建新版本
    docs.edit_times["NODE1"] = "3000"
    batch = service.run()
    db.commit()
    assert db.query(SchemeVersion).count() == 1
    assert batch.detail["items"][0]["result"] == RESULT_UNCHANGED


def test_sync_skips_when_unchanged(db):
    """编辑时间没变 ⇒ 直接跳过，不抓正文。"""
    docs = make_docs(edit_times={"NODE1": "1000"})
    make_source(db, docs, last_edit_time="1000")
    service = FeishuSchemeSyncService(db, docs, actor_id="H")
    batch = service.run()
    db.commit()
    assert batch.detail["items"][0]["result"] == RESULT_UNCHANGED
    assert db.query(SchemeVersion).count() == 0


def test_sync_failure_does_not_break_other_sources(db):
    """一个来源失败**不影响**另一个来源成功。"""
    docs = make_docs(edit_times={"NODE1": "2000"})
    good = make_source(db, docs, last_edit_time="1000")
    bad = FeishuSchemeSource(
        node_token="NODE_BAD",
        obj_token="OBJ_BAD",
        obj_type="docx",
        title="坏来源",
        space_id="SPACE1",
        source_url="",
        department_id="D2",
        period_type="monthly",
        scheme_code="SCHEME_D2_MONTHLY",
        binding_mode=MODE_MANUAL,
        active=True,
        last_edit_time="1000",
    )
    db.add(bad)
    db.commit()

    service = FeishuSchemeSyncService(db, docs, actor_id="H")
    batch = service.run()
    db.commit()
    assert batch.failed_count == 1
    assert batch.published_count == 1
    results = {i["source_id"]: i["result"] for i in batch.detail["items"]}
    assert results[good.id] == RESULT_SYNCED
    assert results[bad.id] == RESULT_FAILED


def test_sync_permission_error_is_recorded(db):
    """权限不足 ⇒ 记进批次与来源的错误字段，**不静默**。"""
    docs = make_docs(edit_times={"NODE1": "2000"}, denied={"OBJ1"})
    source = make_source(db, docs, last_edit_time="1000")
    service = FeishuSchemeSyncService(db, docs, actor_id="H")
    batch = service.run()
    db.commit()
    assert batch.status == "failed"
    item = batch.detail["items"][0]
    assert item["result"] == RESULT_FAILED
    assert item["category"] == "permission"
    assert "权限" in source.last_error


# ─────────────────────────────────────────────────────────────
# 6. 生效期顺延
# ─────────────────────────────────────────────────────────────


def test_previous_version_period_is_kept_unchanged(db):
    """新版本接在旧版本之后；**旧版本生效期一个字节都不改**。

    ⚠️ 用户明确选择「保留旧版本，旧版本不动」⇒ 顺延的是新版本。
    """
    docs = make_docs(edit_times={"NODE1": "2000"})
    make_source(db, docs, last_edit_time="1000")
    service = FeishuSchemeSyncService(db, docs, actor_id="H")
    service.run()
    db.commit()

    first_binding = db.scalar(select(SchemeBinding))
    original_from = first_binding.effective_from
    original_to = first_binding.effective_to

    # 文档内容**真的改了**（改了分值）且编辑时间变化 ⇒ 生成第二个版本
    docs.content["OBJ1"] = (build_docx(changed=True), "方案.docx")
    docs.edit_times["NODE1"] = "4000"
    service.run()
    db.commit()

    versions = db.scalars(
        select(SchemeVersion).order_by(SchemeVersion.version_no)
    ).all()
    assert len(versions) == 2
    bindings = db.scalars(
        select(SchemeBinding).order_by(SchemeBinding.effective_from)
    ).all()
    assert len(bindings) == 2

    old, new = bindings
    # 旧版本**完全没变**
    assert old.effective_from == original_from
    assert old.effective_to == original_to
    # 新版本接在旧版本之后 ⇒ 首尾相接、不重叠
    assert new.effective_from == old.effective_to + timedelta(days=1)
    # 新版本仍然是合法区间
    assert new.effective_from <= new.effective_to


def test_next_effective_window_monthly():
    start, end = next_effective_window("monthly", date(2026, 11, 20))
    assert start == date(2026, 11, 21)
    assert end == date(2026, 11, 30)


def test_next_effective_window_quarterly():
    start, end = next_effective_window("quarterly", date(2026, 11, 20))
    assert start == date(2026, 11, 21)
    assert end == date(2026, 12, 31)


def test_next_effective_window_annual():
    start, end = next_effective_window("annual", date(2026, 11, 20))
    assert start == date(2026, 11, 21)
    assert end == date(2026, 12, 31)


def test_next_effective_window_after_previous_end():
    """有旧版本时，新版本从旧版本结束日的次日开始。"""
    start, end = next_effective_window(
        "monthly", date(2026, 11, 20), after=date(2026, 11, 30)
    )
    assert start == date(2026, 12, 1)
    assert end == date(2026, 12, 31)


# ─────────────────────────────────────────────────────────────
# 6b. 缺等级规则 ⇒ 只建草稿
# ─────────────────────────────────────────────────────────────


def test_missing_grade_rules_creates_draft_only(db):
    """文档没有等级表 ⇒ 不自动发布（否则主管无法判定等级）。"""
    docs = FakeDocsClient(
        nodes={"NODE1": NODE},
        content={"OBJ1": (build_docx(with_grades=False), "方案.docx")},
        edit_times={"NODE1": "2000"},
    )
    make_source(db, docs, last_edit_time="1000")
    service = FeishuSchemeSyncService(db, docs, actor_id="H")
    batch = service.run()
    db.commit()

    item = batch.detail["items"][0]
    assert item["result"] == RESULT_SKIPPED
    assert "等级" in item["reason"]

    version = db.scalar(select(SchemeVersion))
    assert version is not None
    assert version.status == "draft"  # 没发布
    # 但草稿确实建了 —— 不能让「同步了但没生效」查无实据
    assert item["draft_created"] is True


# ─────────────────────────────────────────────────────────────
# 7. 草稿模式（关闭自动发布）
# ─────────────────────────────────────────────────────────────


def test_auto_publish_disabled_creates_draft_only(db, monkeypatch):
    """关闭自动发布 ⇒ 只建草稿，等人工确认。"""
    from backend.app.core import config

    monkeypatch.setattr(config.settings, "feishu_scheme_sync_auto_publish", False)
    docs = make_docs(edit_times={"NODE1": "2000"})
    make_source(db, docs, last_edit_time="1000")
    service = FeishuSchemeSyncService(db, docs, actor_id="H")
    batch = service.run()
    db.commit()
    assert batch.detail["items"][0]["result"] == RESULT_SKIPPED
    version = db.scalar(select(SchemeVersion))
    assert version is not None and version.status == "draft"


# ─────────────────────────────────────────────────────────────
# 8. 发布校验闸门
# ─────────────────────────────────────────────────────────────


def test_direct_points_requires_review_note(db):
    """direct_points 方案必须有核对说明才能发布 —— 全自动模式要自动填。"""
    payload = {
        "code": "X",
        "name": "n",
        "output_mode": "narrative",
        "scoring_mode": "direct_points",
        "binding": {
            "department_id": "D",
            "position": "",
            "period_type": "monthly",
            "effective_from": "2027-01-01",
            "effective_to": "2027-12-31",
        },
        "metrics": [
            {
                "code": "K1",
                "name": "m",
                "type": "narrative",
                "weight": 10,
                "max_score": 10,
                "scoring_method": "manager_review",
                "scoring_description": "原文",
            }
        ],
        "grades": [],
        "fields": [],
    }
    errors = scheme_service.validate_payload(db, payload, draft=False)
    assert any("核对" in e for e in errors)


def test_sync_auto_fills_review_note(db):
    """自动同步生成的版本带核对说明（记录来源与指纹）。"""
    docs = make_docs(edit_times={"NODE1": "2000"})
    make_source(db, docs, last_edit_time="1000")
    service = FeishuSchemeSyncService(db, docs, actor_id="H")
    service.run()
    db.commit()
    version = db.scalar(select(SchemeVersion))
    document = version.payload["document"]
    assert document["review_confirmed"] is True
    assert "飞书文档" in document["review_note"]
    assert document["content_hash"]
    assert document["source"]["obj_token"] == "OBJ1"


# ─────────────────────────────────────────────────────────────
# 9. 批次落库
# ─────────────────────────────────────────────────────────────


def test_sync_batch_persisted(db):
    docs = make_docs(edit_times={"NODE1": "2000"})
    make_source(db, docs, last_edit_time="1000")
    service = FeishuSchemeSyncService(db, docs, actor_id="H", trigger_type="manual")
    service.run()
    db.commit()
    batch = db.scalar(select(FeishuSchemeSyncBatch))
    assert batch is not None
    assert batch.trigger_type == "manual"
    assert batch.status == "succeeded"
    assert batch.finished_at is not None


# ─────────────────────────────────────────────────────────────
# 10. 自动发起考核
# ─────────────────────────────────────────────────────────────


def test_auto_start_task_disabled_by_default(db):
    """默认**不**自动发起考核 —— 「发布方案」≠「给全员开考核」。"""
    docs = make_docs(edit_times={"NODE1": "2000"})
    make_source(db, docs, last_edit_time="1000")
    service = FeishuSchemeSyncService(db, docs, actor_id="H")
    batch = service.run()
    db.commit()
    item = batch.detail["items"][0]
    assert "task" not in item


def test_auto_start_task_is_idempotent(db, monkeypatch):
    """开启自动发起后，同周期**不重复发起**。"""
    from backend.app.core import config

    monkeypatch.setattr(config.settings, "feishu_scheme_sync_auto_start_task", True)
    docs = make_docs(edit_times={"NODE1": "2000"})
    make_source(db, docs, last_edit_time="1000")
    service = FeishuSchemeSyncService(db, docs, actor_id="H")

    batch = service.run()
    db.commit()
    item = batch.detail["items"][0]
    task = item.get("task") or {}
    # 方案生效期从「次日」开始，可能不在当前周期内 ⇒ 不发起。
    # 无论发不发起，都**不能重复发起**。
    assert task.get("started") in (True, False)
    if task.get("started"):
        from backend.app.domain.models import AssessmentTask

        assert db.query(AssessmentTask).count() == 1
        second = service.run()
        db.commit()
        assert db.query(AssessmentTask).count() == 1
        second_task = second.detail["items"][0].get("task") or {}
        assert second_task.get("started") is False


# ─────────────────────────────────────────────────────────────────────────
# 11. 自动发现接进同步轮次
# ─────────────────────────────────────────────────────────────────────────


def test_sync_runs_auto_discover_first(db, monkeypatch):
    """⚠️ 回归：``run()`` 必须**先自动发现再同步**。

    顺序错了会变成「本轮发现、下轮才同步」——
    新部门的方案要多等一个周期才生效。
    """
    calls: list[str] = []

    from backend.app.services import feishu_scheme_sync as sync_mod

    original = sync_mod.FeishuSchemeSyncService._auto_discover

    def spy(self):
        calls.append("discover")
        return original(self)

    monkeypatch.setattr(sync_mod.FeishuSchemeSyncService, "_auto_discover", spy)

    docs = make_docs(edit_times={"NODE1": "2000"})
    make_source(db, docs, last_edit_time="1000")
    service = FeishuSchemeSyncService(db, docs, actor_id="H")
    service.run()
    db.commit()

    assert calls == ["discover"], "同步轮次里应调用一次自动发现"


def test_discover_result_recorded_in_batch_detail(db):
    """⚠️ 回归：自动发现结果要记进批次明细 —— 否则运维看不到发现情况。"""
    docs = make_docs(edit_times={"NODE1": "2000"})
    make_source(db, docs, last_edit_time="1000")
    service = FeishuSchemeSyncService(db, docs, actor_id="H")
    batch = service.run()
    db.commit()

    detail = batch.detail or {}
    assert "discover" in detail, "批次明细里应有自动发现结果"
    discover = detail["discover"]
    assert discover.get("status") in ("ok", "skipped", "failed")
    assert "discovered" in discover


def test_discover_failure_does_not_break_sync(db, monkeypatch):
    """⚠️ 回归：自动发现失败**不能**拖垮整轮同步。

    场景：应用没被加入知识空间（131006）。
    ⇒ 发现跳过，但已绑定来源照常同步。
    """
    docs = make_docs(edit_times={"NODE1": "2000"})
    make_source(db, docs, last_edit_time="1000")
    service = FeishuSchemeSyncService(db, docs, actor_id="H")

    def boom(self):
        raise RuntimeError("模拟发现炸了")

    monkeypatch.setattr(
        FeishuSchemeSyncService, "_auto_discover", boom, raising=True
    )
    batch = service.run()
    db.commit()

    # 发现炸了 → 整轮同步仍要有结果，且不抛异常
    assert batch is not None
    assert batch.status in ("succeeded", "failed", "partial")
    assert len(batch.detail["items"]) == 1


def test_discover_skipped_when_sync_disabled(db, monkeypatch):
    """总开关关闭时不该跑自动发现。"""
    from backend.app.core import config

    monkeypatch.setattr(config.settings, "feishu_scheme_sync_enabled", False)
    docs = make_docs(edit_times={"NODE1": "2000"})
    make_source(db, docs, last_edit_time="1000")
    service = FeishuSchemeSyncService(db, docs, actor_id="H")
    result = service._auto_discover()
    assert result["status"] == "skipped"
    assert "discovered" in result
