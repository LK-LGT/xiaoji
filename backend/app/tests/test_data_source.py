"""P1-5 acceptance: 数据源白名单 —— 默认不信任 / 角色可读 / 算分闸门 / 字段脱敏。"""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app.domain.models import Base, DataSourcePolicy, Employee
from backend.app.services import data_source_service as svc


@pytest.fixture
def db():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    s.flush()
    s.commit()
    yield s
    s.close()
    engine.dispose()


def test_unknown_source_is_not_readable(db):
    """默认不信任：未配置的数据源不可读。"""
    assert svc.get_policy(db, "unknown_source") is None
    assert svc.is_readable(db, "unknown_source", frozenset({"hr", "manager"})) is False


def test_configured_source_is_readable_by_allowed_role(db):
    """角色可读：配置里声明的角色可以读。"""
    svc.upsert_policy(
        db, source_code="attendance", allowed=True, allowed_roles="hr,manager"
    )
    db.commit()
    assert svc.is_readable(db, "attendance", frozenset({"manager"})) is True


def test_role_not_in_list_cannot_read(db):
    """不在允许角色里的读不到。"""
    svc.upsert_policy(
        db, source_code="attendance", allowed=True, allowed_roles="hr"
    )
    db.commit()
    assert svc.is_readable(db, "attendance", frozenset({"manager"})) is False


def test_hr_can_always_read(db):
    """HR 永远可读（结算与申诉需要完整数据）。"""
    svc.upsert_policy(
        db, source_code="anything", allowed=False, allowed_roles=""
    )
    db.commit()
    assert svc.is_readable(db, "anything", frozenset({"hr"})) is True


def test_scoring_entrance_follows_allowed_flag(db):
    """算分入口闸门：allowed=False 的源不能进算分集。"""
    svc.upsert_policy(db, source_code="attendance", allowed=False, allowed_roles="hr")
    db.commit()
    assert svc.can_enter_scoring(db, "attendance") is False

    svc.upsert_policy(db, source_code="attendance", allowed=True, allowed_roles="hr")
    db.commit()
    assert svc.can_enter_scoring(db, "attendance") is True


def test_unconfigured_source_cannot_enter_scoring(db):
    """未配置的源一律不能进算分集。"""
    assert svc.can_enter_scoring(db, "never_configured") is False


def test_apply_redaction_masks_declared_fields(db):
    """字段脱敏：声明的字段读出时被替换为 ***。"""
    svc.upsert_policy(
        db,
        source_code="manual_upload",
        allowed=True,
        allowed_roles="hr",
        redacted_fields="mobile,email",
    )
    db.commit()
    payload = {"id": "E1", "summary": "s", "mobile": "13800000000", "email": "a@b.c"}
    out = svc.apply_redaction(db, "manual_upload", payload)
    assert out["mobile"] == "***"
    assert out["email"] == "***"
    assert out["id"] == "E1"
    assert out["summary"] == "s"
    # 原对象不被就地修改（调用方可能复用）
    assert payload["mobile"] == "13800000000"


def test_redaction_ignores_fields_not_declared(db):
    """未声明的字段不受影响。"""
    svc.upsert_policy(
        db,
        source_code="manual_upload",
        allowed=True,
        allowed_roles="hr",
        redacted_fields="mobile",
    )
    db.commit()
    out = svc.apply_redaction(db, "manual_upload", {"email": "a@b.c", "mobile": "1"})
    assert out["email"] == "a@b.c"
    assert out["mobile"] == "***"


def test_upsert_creates_then_updates(db):
    """upsert 幂等：先建后改，而不是重复插入。"""
    svc.upsert_policy(db, source_code="new_src", allowed=True, allowed_roles="hr")
    db.commit()
    svc.upsert_policy(db, source_code="new_src", allowed=False, allowed_roles="")
    db.commit()
    rows = db.query(DataSourcePolicy).filter(DataSourcePolicy.source_code == "new_src").all()
    assert len(rows) == 1
    assert rows[0].allowed is False


def test_empty_source_code_rejected(db):
    """空标识被拒。"""
    with pytest.raises(svc.DataSourceError, match="不能为空"):
        svc.upsert_policy(db, source_code="   ", allowed=True)


def test_load_policies_contains_defaults(db):
    """默认项始终出现在列表里（表还没建时也有确定语义）。"""
    policies = svc.load_policies(db)
    assert "attendance" in policies
    assert "feishu_approval" in policies
    assert "manual_upload" in policies


def test_load_policies_table_overrides_default(db):
    """表里的配置覆盖内置默认。"""
    svc.upsert_policy(
        db,
        source_code="attendance",
        allowed=False,
        allowed_roles="hr",
        note="本期停用",
    )
    db.commit()
    policies = svc.load_policies(db)
    assert policies["attendance"]["allowed"] is False
    assert policies["attendance"]["configured"] is True
    assert policies["attendance"]["note"] == "本期停用"


def test_serialize_shape(db):
    """序列化输出给前端的是驼峰 + 列表形状。"""
    svc.upsert_policy(
        db,
        source_code="attendance",
        allowed=True,
        allowed_roles="hr, manager",
        redacted_fields="mobile",
    )
    db.commit()
    out = svc.serialize(svc.get_policy(db, "attendance"))
    assert out["sourceCode"] == "attendance"
    assert out["allowedRoles"] == ["hr", "manager"]
    assert out["redactedFields"] == ["mobile"]
