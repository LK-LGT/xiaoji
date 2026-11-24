"""绩效评定表附件（FS-6）的测试。

⚠️ **核心断言**：
1. Excel 评定表能生成，且内容与 ``Score`` 表一致（**只读真值**）；
2. 附件是**独立的一条 file 消息**（飞书消息类型互斥）；
3. PDF 不可用时**自动降级**为 Excel，而不是整条推送失败。
"""

from __future__ import annotations

import io

import pytest
from openpyxl import load_workbook
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app.domain.models import (
    AssessmentRecord,
    AssessmentTask,
    Base,
    Employee,
    Score,
)


@pytest.fixture()
def db():
    engine = create_engine(
        "sqlite://",
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


@pytest.fixture()
def record(db) -> AssessmentRecord:
    """造一条「已确认等级」的考核记录 + 分数。"""
    db.add(Employee(id="E1", name="张三"))
    db.add(
        AssessmentTask(
            id="T1",
            name="2026年11月绩效考核",
            period="2026-11",
            status="confirmed",
        )
    )
    db.add(
        AssessmentRecord(
            id="R1",
            task_id="T1",
            employee_id="E1",
            status="confirmed",
            confirmed_rating="A",
            manager_comment="表现良好",
        )
    )
    db.add(
        Score(
            record_id="R1",
            total=88.5,
            per_metric_json=[
                {
                    "metric_code": "M1",
                    "metric_name": "剪辑质量",
                    "raw_weight": 60,
                    "normalized_weight": 0.6,
                    "value": 90,
                    "target": 100,
                    "ratio": 0.9,
                    "level": "优",
                    "level_desc": "达标",
                    "level_score": 1.0,
                    "contribution": 60.0,
                    "unit": "分",
                    "source": "manual",
                    "as_of": "2026-11-30",
                }
            ],
            trace_json={},
        )
    )
    db.commit()
    return db.get(AssessmentRecord, "R1")


class TestRatingExcel:
    def test_generates_valid_xlsx(self, db, record):
        from backend.app.services.feishu_rating_export import build_rating_excel

        export = build_rating_excel(db, record)
        assert export.file_type == "xlsx"
        assert export.filename.endswith(".xlsx")
        assert len(export.content) > 0

    def test_workbook_has_two_sheets(self, db, record):
        from backend.app.services.feishu_rating_export import build_rating_excel

        export = build_rating_excel(db, record)
        workbook = load_workbook(io.BytesIO(export.content))
        assert workbook.sheetnames == ["概览", "指标明细"]

    def test_overview_contains_rating_and_total(self, db, record):
        """⚠️ 概览必须含等级与总分 —— 这是用户最关心的两个数。"""
        from backend.app.services.feishu_rating_export import build_rating_excel

        export = build_rating_excel(db, record)
        workbook = load_workbook(io.BytesIO(export.content))
        overview = workbook["概览"]
        pairs = {
            row[0].value: row[1].value
            for row in overview.iter_rows(min_row=2, max_col=2)
            if row[0].value
        }
        assert pairs["最终等级"] == "A"
        assert pairs["总分"] == "88.5"
        assert pairs["员工姓名"] == "张三"
        assert pairs["考核周期"] == "2026-11"

    def test_metric_detail_rows_match_score_table(self, db, record):
        """⚠️ **真值一致** —— 附件里的数字必须与 ``Score`` 表完全一致。

        附件是给人看的副本；真值永远在 ``Score`` 里。
        """
        from backend.app.services.feishu_rating_export import build_rating_excel

        export = build_rating_excel(db, record)
        workbook = load_workbook(io.BytesIO(export.content))
        detail = workbook["指标明细"]
        rows = list(detail.iter_rows(min_row=2, values_only=True))
        assert len(rows) == 1
        row = rows[0]
        assert row[0] == "M1"          # 指标编码
        assert row[1] == "剪辑质量"     # 指标名称
        assert row[7] == "优"          # 档位
        assert row[10] == "60"         # 贡献分

    def test_filename_contains_employee_and_period(self, db, record):
        from backend.app.services.feishu_rating_export import build_rating_excel

        export = build_rating_excel(db, record)
        assert "张三" in export.filename
        assert "2026-11" in export.filename

    def test_filename_has_no_illegal_characters(self, db, record):
        """⚠️ 文件名里的 ``/`` ``\\`` 会让部分系统截断。"""
        from backend.app.services.feishu_rating_export import build_rating_excel

        export = build_rating_excel(db, record)
        for ch in '\\/:*?"<>|':
            assert ch not in export.filename

    def test_export_does_not_write_to_score(self, db, record):
        """⚠️ **导出只读** —— 绝不能改动 ``Score``（铁律：分数唯一真值）。"""
        from backend.app.services.feishu_rating_export import build_rating_excel

        before = db.query(Score).filter_by(record_id="R1").one().total
        build_rating_excel(db, record)
        db.refresh(db.query(Score).filter_by(record_id="R1").one())
        assert db.query(Score).filter_by(record_id="R1").one().total == before


class TestAttachmentDispatch:
    def test_xlsx_attachment_is_sent(self, db, record):
        """⚠️ N8 通知要**额外**发一条 file 消息。"""
        from backend.app.services.notification_service import (
            _send_rating_attachment,
        )

        class _StubSender:
            def __init__(self):
                self.uploaded = []
                self.files = []

            def upload_file(self, filename, content, *, file_type="xlsx"):
                self.uploaded.append((filename, content, file_type))
                return "file_key-1"

            def send_file_message(self, receive_id, file_key, **kwargs):
                self.files.append((receive_id, file_key, kwargs))
                return {"code": 0}

        sender = _StubSender()
        _send_rating_attachment(db, sender, "ou_1", "open_id", record)

        assert len(sender.uploaded) == 1
        assert sender.uploaded[0][2] == "xlsx"
        assert len(sender.files) == 1
        assert sender.files[0][1] == "file_key-1"

    def test_pdf_falls_back_to_xlsx_when_unavailable(self, db, record, monkeypatch):
        """⚠️ PDF 不可用时**降级**，不能整条推送失败。"""
        import backend.app.services.feishu_rating_export as export_mod

        def _boom(db_, record_):
            raise RuntimeError("缺 reportlab 依赖")

        monkeypatch.setattr(export_mod, "build_rating_pdf", _boom)

        result = export_mod.build_rating_attachment(db, record, file_type="pdf")
        assert result.file_type == "xlsx"


class TestClientFileApi:
    def test_upload_file_returns_file_key(self):
        """⚠️ 上传返回 ``file_key``，消息体里用的就是它。"""
        from backend.app.services.feishu_client import FeishuClient

        class _Transport:
            def handle_request(self, request):
                import httpx

                # ⚠️ 客户端先取 tenant token —— mock 必须先答 token 请求。
                if "tenant_access_token" in request.url.path:
                    return httpx.Response(
                        200,
                        json={
                            "code": 0,
                            "tenant_access_token": "tok-1",
                            "expire": 7200,
                        },
                    )
                return httpx.Response(
                    200, json={"code": 0, "data": {"file_key": "fk-1"}}
                )

        client = FeishuClient(
            app_id="a", app_secret="s", transport=_Transport(), sleep=lambda _: None
        )
        assert client.upload_file("a.xlsx", b"x") == "fk-1"

    def test_send_file_message_uses_file_msg_type(self):
        """⚠️ 附件消息的 ``msg_type`` 必须是 ``file``。"""
        from backend.app.services.feishu_client import FeishuClient

        captured: dict = {}

        class _Transport:
            def handle_request(self, request):
                import httpx

                if "tenant_access_token" in request.url.path:
                    return httpx.Response(
                        200,
                        json={
                            "code": 0,
                            "tenant_access_token": "tok-1",
                            "expire": 7200,
                        },
                    )
                captured["path"] = request.url.path
                captured["content"] = request.content
                return httpx.Response(200, json={"code": 0, "data": {}})

        client = FeishuClient(
            app_id="a", app_secret="s", transport=_Transport(), sleep=lambda _: None
        )
        client.send_file_message("ou_1", "fk-1")

        assert captured["path"] == "/open-apis/im/v1/messages"
        # ⚠️ httpx 序列化 JSON **不带空格** —— 断言别写 `"msg_type": "file"`。
        assert b'"msg_type":"file"' in captured["content"]
        assert b"fk-1" in captured["content"]
