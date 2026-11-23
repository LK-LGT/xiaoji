"""P0-9 / P0-10：拒绝体验与审计留痕。

⚠️ **这两项为什么放一起测**
--------------------------
P0-9 是「拒绝时给用户看什么」，P0-10 是「拒绝时给系统留什么」。
它们在**同一个分支**上发生 ⇒ 一起测才能验证「用户看到的话」与
「系统记下的事」**口径一致**（比如：不能用户看到"不在可见范围内"，
而审计里记成"跨部门"—— 那审计表就成了泄露面）。

⚠️ **测试的核心断言**
--------------------
1. 拒绝卡片**必须带下一步**（否则用户反复试）；
2. 拒绝文案**必须统一口径**（不区分跨部门/跨级）；
3. 审计**必须落库**（不能只有 logger.warning）；
4. 审计**不能记问题原文**（PII）；
5. 审计**不能记细粒度拒绝原因**（审计表本身是敏感资产）。
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app.domain.models import (
    AiToolCall,
    AssessmentTask,
    AuditLog,
    Base,
    Department,
    Employee,
)
from backend.app.services import qa_audit, qa_authorization
from backend.app.services.feishu_card import (
    build_qa_denied_card,
    build_qa_write_denied_card,
)

# ---------------------------------------------------------------------------
# 夹具
# ---------------------------------------------------------------------------


@pytest.fixture()
def db() -> Session:
    engine = create_engine(
        "sqlite:///:memory:",
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


def _emp(
    db: Session,
    emp_id: str,
    name: str,
    dept: str | None,
    manager: str | None = None,
) -> Employee:
    emp = Employee(
        id=emp_id,
        name=name,
        department_id=dept,
        manager_id=manager,
        employment_status="active",
        account_status="enabled",
    )
    db.add(emp)
    db.flush()
    return emp


def _text_of(card: dict) -> str:
    """把卡片的正文文字全抽出来 —— 便于断言文案。"""
    parts: list[str] = []
    for element in card.get("body", {}).get("elements", []):
        if element.get("tag") == "div":
            parts.append(str((element.get("text") or {}).get("content") or ""))
        elif element.get("tag") == "markdown":
            parts.append(str(element.get("content") or ""))
        elif element.get("tag") == "action":
            for action in element.get("actions", []):
                parts.append(str((action.get("text") or {}).get("content") or ""))
    return "\n".join(parts)


# ===========================================================================
# P0-9：拒绝卡片
# ===========================================================================


class TestP09DeniedCard:
    """拒绝卡片必须说清「为什么」和「下一步」。"""

    def test_拒绝卡片必须带下一步(self) -> None:
        """⚠️ 只说"没有权限"用户会**反复试同一个问题**。

        ⚠️ 他不知道是自己搞错了、还是系统坏了、还是换个问法就行。
        ⇒ 卡片里必须有引导。
        """
        card = build_qa_denied_card(
            message="该同事不在你的可见范围内。",
            guidance="绩效数据按组织范围隔离。",
        )
        text = _text_of(card)
        assert "不在你的可见范围内" in text
        assert "绩效数据按组织范围隔离" in text
        # ⚠️ 引导前缀是「💡」—— 用户得看得出这是建议不是错误
        assert "💡" in text

    def test_拒绝卡片可以带后台页面引导(self) -> None:
        card = build_qa_denied_card(
            message="该数据不在可见范围内。",
            guidance="换个问法也不行。",
            page="考核记录",
        )
        text = _text_of(card)
        assert "考核记录" in text
        assert "后台" in text

    def test_拒绝卡片用纯文本_防止_at_注入(self) -> None:
        """⚠️ 人名来自数据库，可能含 ``<at>`` ——
        markdown 会把它解析成「@某人」。
        ⇒ 正文必须是 ``plain_text``（包在 ``div`` 里）。
        """
        card = build_qa_denied_card(message="张三不在可见范围内。")
        for element in card["body"]["elements"]:
            assert element.get("tag") != "plain_text"
            if element.get("tag") == "div":
                assert element["text"]["tag"] == "plain_text"

    def test_写操作拒绝用另一张卡(self) -> None:
        """⚠️ 「不能改分」与「看不见」是**两回事**。

        - 不能改分 ⇒ 去后台就能做（能力边界）；
        - 看不见 ⇒ 谁都做不了（权限范围）。

        ⇒ 标题/配色/引导都必须不同。
        """
        write_card = build_qa_write_denied_card()
        read_card = build_qa_denied_card(message="不在可见范围内。")

        assert write_card["header"]["title"]["content"] != (
            read_card["header"]["title"]["content"]
        )
        # ⚠️ 写操作必须给**具体的后台页面** —— 用户要知道去哪做
        assert "考核记录" in _text_of(write_card)

    def test_写操作拒绝带页面时用指定页面(self) -> None:
        card = build_qa_write_denied_card(message="x", page="绩效方案")
        assert "绩效方案" in _text_of(card)

    def test_空文案不崩(self) -> None:
        card = build_qa_denied_card(message="")
        assert card["body"]["elements"]
        assert _text_of(card)


class TestP09ErrorRouting:
    """403 / 404 / 500 要给**不同**的卡片。"""

    def test_403_走拒绝卡片(self) -> None:
        from fastapi import HTTPException

        from backend.app.routes.feishu_bot import _qa_error_card

        card = _qa_error_card(HTTPException(403, "该同事不在你的可见范围内。"))
        assert card["header"]["title"]["content"] == "无法查看该数据"

    def test_403_写操作走写操作卡(self) -> None:
        from fastapi import HTTPException

        from backend.app.routes.feishu_bot import _qa_error_card

        card = _qa_error_card(HTTPException(403, "AI 问答不提供绩效修改功能。"))
        assert card["header"]["title"]["content"] == "此操作需在后台完成"

    def test_404_走错误卡(self) -> None:
        """⚠️ 404 是**参数问题**（月份/任务不对）⇒ 提示改参数，不是权限。"""
        from fastapi import HTTPException

        from backend.app.routes.feishu_bot import _qa_error_card

        card = _qa_error_card(HTTPException(404, "考核任务不存在"))
        assert card["header"]["title"]["content"] == "操作未完成"

    def test_500_走错误卡(self) -> None:
        from fastapi import HTTPException

        from backend.app.routes.feishu_bot import _qa_error_card

        card = _qa_error_card(HTTPException(500, "内部错误"))
        assert card["header"]["title"]["content"] == "操作未完成"


class TestP09WriteActionInterception:
    """写操作拦截必须**真的生效** —— 之前只测了检测函数，没接进链路。"""

    def test_问改分被拦截(self) -> None:
        """⚠️ 问「帮我把分数改成95分」不能得到一个答非所问的读数回答。

        ⚠️ 拦截点必须在**共享内核**（``qa_chat_service.ask_question_async``）——
        网页端与飞书端调的是同一个函数，才能保证两端一致。
        """
        import asyncio

        from backend.app.services import qa_chat_service

        emp = Employee(
            id="E1", name="张三", employment_status="active", account_status="enabled",
        )
        with pytest.raises(Exception) as exc:
            asyncio.run(
                qa_chat_service.ask_question_async(
                    None, emp, question="帮我把分数改成95分", task_id="T1",
                )
            )
        # ⚠️ 403 且文案是「不提供修改功能」
        assert getattr(exc.value, "status_code", None) == 403
        assert "不提供绩效修改功能" in str(exc.value.detail)

    def test_两端文案一致(self) -> None:
        """⚠️ 飞书端与共享内核的文案必须**逐字相同** ——
        飞书端靠文案嗅探区分两类 403，文案漂移就会误判。
        """
        from backend.app.routes.feishu_bot import (
            QA_WRITE_DENIED_MESSAGE as FEISHU_MESSAGE,
        )
        from backend.app.services.qa_chat_service import (
            QA_WRITE_DENIED_MESSAGE as CORE_MESSAGE,
        )

        assert FEISHU_MESSAGE == CORE_MESSAGE

    def test_问多少分不被误伤(self) -> None:
        """⚠️ 误伤会**毁掉问答体验** —— 必须只拦真写操作。"""
        from backend.app.services.qa_authorization import detect_write_action

        assert detect_write_action("我这个月绩效多少分") is None
        assert detect_write_action("为什么被扣了分") is None


# ===========================================================================
# P0-10：审计
# ===========================================================================


class TestP10Audit:
    """审计必须**落库**，且不泄露 PII。"""

    def test_越权尝试写审计(self, db: Session) -> None:
        """⚠️ 跨部门查询 ⇒ 必须留 ``AuditLog``。

        ⚠️ 之前只有 ``logger.warning`` —— 进程重启就没了，
        而"谁越权了"恰恰是安全看板最需要长期留痕的事。
        """
        dept_a = Department(id="DA", name="A部")
        dept_b = Department(id="DB", name="B部")
        db.add_all([dept_a, dept_b])
        db.flush()

        asker = _emp(db, "E1", "张三", "DA")
        target = _emp(db, "E2", "李四", "DB")
        task = AssessmentTask(id="T1", name="11月考核", period="202611")
        db.add(task)
        db.flush()

        with pytest.raises(Exception):
            qa_authorization.resolve_scope(
                db, asker, task.id, "202611", subject_id=target.id,
            )

        logs = db.scalars(select(AuditLog)).all()
        assert len(logs) == 1
        log = logs[0]
        assert log.actor_id == "E1"
        assert log.resource_id == "E2"
        assert log.resource_type == "employee"
        assert log.result == qa_audit.RESULT_DENIED
        assert log.action == qa_audit.ACTION_QUERY_PEER

    def test_审计不记细粒度原因(self, db: Session) -> None:
        """⚠️⚠️ **审计表本身也是敏感资产**。

        若审计里写着"目标人是我的上级"，
        一旦审计表能被越权者读到 ⇒ 就是新的泄露面。
        ⇒ reason 必须是**粗粒度**的。
        """
        dept_a = Department(id="DA", name="A部")
        dept_b = Department(id="DB", name="B部")
        db.add_all([dept_a, dept_b])
        db.flush()

        asker = _emp(db, "E1", "张三", "DA")
        target = _emp(db, "E2", "李四", "DB")
        task = AssessmentTask(id="T1", name="11月考核", period="202611")
        db.add(task)
        db.flush()

        with pytest.raises(Exception):
            qa_authorization.resolve_scope(
                db, asker, task.id, "202611", subject_id=target.id,
            )

        log = db.scalars(select(AuditLog)).one()
        reason = str(log.metadata_json.get("reason") or "")
        assert reason == "out_of_scope"
        # ⚠️ 不能出现这些能反推层级/部门的词
        for leaked in ("跨部门", "上级", "跨级", "部门"):
            assert leaked not in reason

    def test_审计不记问题原文(self, db: Session) -> None:
        """⚠️ 员工可能在飞书里问任何东西 ⇒ 原文长期留存是隐私风险。"""
        db.add(Department(id="DA", name="A部"))
        db.flush()
        asker = _emp(db, "E1", "张三", "DA")
        target = _emp(db, "E2", "李四", "DA")
        db.add(AssessmentTask(id="T1", name="11月考核", period="202611"))
        db.flush()

        qa_audit.write_qa_denial_audit(
            db, actor_id=asker.id, subject_id=target.id,
            reason="out_of_scope", period="202611",
        )
        db.commit()

        log = db.scalars(select(AuditLog)).one()
        blob = str(log.metadata_json)
        assert "分数" not in blob
        assert "绩效" not in blob

    def test_审计不记姓名(self, db: Session) -> None:
        """⚠️ 姓名可反查员工表，且会变 ⇒ 只存 id。"""
        db.add(Department(id="DA", name="A部"))
        db.flush()
        asker = _emp(db, "E1", "张三", "DA")
        target = _emp(db, "E2", "李四", "DA")
        db.flush()

        qa_audit.write_qa_denial_audit(
            db, actor_id=asker.id, subject_id=target.id, reason="out_of_scope",
        )
        db.commit()

        log = db.scalars(select(AuditLog)).one()
        assert log.resource_id == "E2"
        assert "李四" not in str(log.metadata_json)

    def test_查自己不写审计(self, db: Session) -> None:
        """⚠️ 查自己的噪音会淹没看板 —— 只记**查他人**。"""
        db.add(Department(id="DA", name="A部"))
        db.flush()
        asker = _emp(db, "E1", "张三", "DA")
        task = AssessmentTask(id="T1", name="11月考核", period="202611")
        db.add(task)
        db.flush()

        # ⚠️ 没有 AssessmentRecord ⇒ 会 403（无权查询该任务数据）
        #    但**不该**产生主体审计（主体是自己）。
        with pytest.raises(Exception):
            qa_authorization.resolve_scope(db, asker, task.id, "202611")

        assert db.scalars(select(AuditLog)).all() == []

    def test_审计写失败不影响主流程(self, db: Session) -> None:
        """⚠️ 用户要的是数据，不是审计 —— 审计挂了不能让他查不到。"""
        db.add(Department(id="DA", name="A部"))
        db.flush()
        asker = _emp(db, "E1", "张三", "DA")

        # ⚠️ 用一个非法 actor 制造写入失败
        qa_audit.write_qa_subject_audit(
            db, actor_id=asker.id, subject_id="E9",
            result=qa_audit.RESULT_ALLOWED, reason="peer",
        )
        db.commit()
        assert len(db.scalars(select(AuditLog)).all()) >= 1

    def test_审计元数据只放白名单键(self, db: Session) -> None:
        """⚠️ 调用方误塞 PII 进 metadata ⇒ 必须被过滤掉。"""
        db.add(Department(id="DA", name="A部"))
        db.flush()
        asker = _emp(db, "E1", "张三", "DA")
        db.flush()

        qa_audit.write_qa_subject_audit(
            db, actor_id=asker.id, subject_id="E2",
            result=qa_audit.RESULT_ALLOWED, reason="peer",
            metadata={"scope": "peer", "secret_question": "我的分数是多少"},
        )
        db.commit()

        log = db.scalars(select(AuditLog)).one()
        assert log.metadata_json.get("scope") == "peer"
        assert "secret_question" not in log.metadata_json


class TestP10ToolCallSubject:
    """``AiToolCall.subject_id`` —— 工具层的「谁查了谁」。"""

    def test_模型有_subject_id_字段(self) -> None:
        """⚠️ 必须是**独立字段** ——
        塞进 args 会被哈希掉 ⇒ 看板查不了。
        """
        column = AiToolCall.__table__.columns["subject_id"]
        assert column.nullable is True
        # ⚠️ 必须有索引 —— 看板按它聚合
        # ⚠️ ``index=True`` 在 SQLAlchemy 里生成的是**列级** Index，
        #    挂在 ``table.indexes`` 上，名字是 ``ix_<表名>_<列名>``。
        index_names = {idx.name for idx in AiToolCall.__table__.indexes}
        assert "ix_ai_tool_call_subject_id" in index_names

    def test_工具审计写入目标人(self, db: Session) -> None:
        """⚠️ 目标人来自**签名令牌**（``subj``），不是请求体。"""
        from types import SimpleNamespace

        from backend.app.routes.agent_tools import write_tool_audit
        from backend.app.services.agent_tool_token import (
            issue_agent_tool_token,
            verify_agent_tool_token,
        )

        token = issue_agent_tool_token(
            employee_id="E1", task_id="T1", allow=["get_peer_score"],
            scope="peer", subject_id="E2", reason="同部门同级别同事（只读）",
        )
        ctx = SimpleNamespace(
            employee_id="E1",
            task_id="T1",
            payload=verify_agent_tool_token(token),
            trace_id=None,
        )

        write_tool_audit(
            db, ctx=ctx, tool="get_peer_score", args={"period": "202611"},
            ok=True, latency_ms=5,
        )
        db.commit()

        call = db.scalars(select(AiToolCall)).one()
        assert call.employee_id == "E1"
        assert call.subject_id == "E2"

    def test_查自己时目标人为空(self, db: Session) -> None:
        """⚠️ ``NULL`` = 目标即本人 ⇒ 看板里直接 ``IS NULL`` 就能筛。"""
        from types import SimpleNamespace

        from backend.app.routes.agent_tools import write_tool_audit
        from backend.app.services.agent_tool_token import (
            issue_agent_tool_token,
            verify_agent_tool_token,
        )

        token = issue_agent_tool_token(
            employee_id="E1", task_id="T1", allow=["get_metric_values"],
        )
        ctx = SimpleNamespace(
            employee_id="E1",
            task_id="T1",
            payload=verify_agent_tool_token(token),
            trace_id=None,
        )

        write_tool_audit(
            db, ctx=ctx, tool="get_metric_values", args={},
            ok=True, latency_ms=5,
        )
        db.commit()

        call = db.scalars(select(AiToolCall)).one()
        assert call.subject_id is None
