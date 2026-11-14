"""阶段一数据库结构迁移测试。

覆盖：
- 已有表缺列时能补齐（create_all 做不到）
- 补列使用正确的 DDL 类型，不能一律退化为 TEXT
- 重复执行幂等
"""

from __future__ import annotations

from sqlalchemy import create_engine, inspect, text

from backend.app.core.migrations import sync_schema


def _legacy_engine():
    """构造"旧版本"数据库：表存在但缺少新列。"""
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE organization_sync_batch (
                    id VARCHAR(64) PRIMARY KEY,
                    status VARCHAR(32) NOT NULL,
                    started_at DATETIME NOT NULL
                )
                """
            )
        )
        conn.execute(
            text(
                """
                CREATE TABLE employee (
                    id VARCHAR(64) PRIMARY KEY,
                    name VARCHAR(128) NOT NULL
                )
                """
            )
        )
    return engine


def test_sync_schema_adds_missing_columns():
    engine = _legacy_engine()
    applied = sync_schema(engine)

    assert applied, "缺失列时应当执行 ALTER"

    inspector = inspect(engine)
    batch_columns = {c["name"] for c in inspector.get_columns("organization_sync_batch")}
    employee_columns = {c["name"] for c in inspector.get_columns("employee")}

    assert "batch_no" in batch_columns
    assert "employees_skipped" in batch_columns
    assert "duration_ms" in batch_columns
    assert "reconciliation_json" in batch_columns

    assert "token_version" in employee_columns
    assert "account_status" in employee_columns
    assert "feishu_user_id" in employee_columns

    engine.dispose()


def test_sync_schema_uses_correct_column_types():
    """回归：类型解析失败时不能静默退化为 TEXT。

    之前 _sqlite_type 引用了未定义的 engine 名，
    异常被 except 吞掉后所有列都变成 TEXT，
    INTEGER / DATETIME / JSON 列类型全部丢失。
    """
    engine = _legacy_engine()
    sync_schema(engine)

    inspector = inspect(engine)
    batch_types = {
        c["name"]: str(c["type"]) for c in inspector.get_columns("organization_sync_batch")
    }
    employee_types = {
        c["name"]: str(c["type"]) for c in inspector.get_columns("employee")
    }

    assert batch_types["employees_skipped"].upper() == "INTEGER"
    assert batch_types["duration_ms"].upper() == "INTEGER"
    assert batch_types["finished_at"].upper() == "DATETIME"
    assert batch_types["reconciliation_json"].upper() == "JSON"
    assert batch_types["batch_no"].upper().startswith("VARCHAR")

    assert employee_types["token_version"].upper() == "INTEGER"
    assert employee_types["created_at"].upper() == "DATETIME"
    assert employee_types["account_status"].upper().startswith("VARCHAR")

    engine.dispose()


def test_sync_schema_is_idempotent():
    engine = _legacy_engine()
    sync_schema(engine)
    second = sync_schema(engine)

    assert second == [], "重复执行不应再产生 ALTER"

    engine.dispose()


def test_sync_schema_skips_missing_tables():
    """缺失的表由 create_all 负责，sync_schema 不应报错。"""
    engine = create_engine("sqlite:///:memory:")
    applied = sync_schema(engine)

    assert applied == []
    assert "hr_department_config" not in inspect(engine).get_table_names()

    engine.dispose()
