"""在隔离库验证正式迁移和SQL级历史保护，不修改运行数据库。"""
import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError

from backend.app.domain.models import Base


@pytest.mark.parametrize("mode", ["missing_url", "missing_target", "wrong_target", "relative_url", "confirmed"])
def test_alembic_requires_confirmed_target(tmp_path, mode):
    backend = Path(__file__).resolve().parents[2]
    target = tmp_path / "target.db"
    env = dict(os.environ)
    env.pop("DATABASE_URL", None)
    env.pop("ALEMBIC_EXPECT_TARGET", None)
    if mode != "missing_url":
        env["DATABASE_URL"] = f"sqlite:///{target.as_posix()}"
    if mode != "missing_target":
        expected = tmp_path / "other.db" if mode == "wrong_target" else target
        env["ALEMBIC_EXPECT_TARGET"] = f"sqlite:{expected.resolve().as_posix()}"
    if mode == "relative_url":
        env["DATABASE_URL"] = "sqlite:///target.db"
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "0027_review_center"],
        cwd=backend,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if mode == "confirmed":
        assert result.returncode == 0, result.stderr
        with create_engine(env["DATABASE_URL"]).connect() as conn:
            assert conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == "0027_review_center"
    else:
        assert result.returncode != 0
        assert "Alembic" in result.stderr
        assert not target.exists()


def test_alembic_rejects_postgres_target_override():
    backend = Path(__file__).resolve().parents[2]
    env = dict(os.environ)
    env.update(
        DATABASE_URL="postgresql://user@approved.invalid:5432/perf?host=other.invalid",
        ALEMBIC_EXPECT_TARGET="postgresql:approved.invalid:5432/perf",
    )
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "current"],
        cwd=backend,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    assert result.returncode != 0
    assert "目标与确认值不一致" in result.stderr


def test_review_migration_and_sql_guards(monkeypatch):
    path = Path(__file__).resolve().parents[2] / "migrations" / "versions" / "0027_review_center.py"
    spec = importlib.util.spec_from_file_location("review_migration", path)
    module = importlib.util.module_from_spec(spec)
    # 迁移执行环境使用 app 包；测试复用相同元数据避免重复声明。
    monkeypatch.setitem(sys.modules, "app", sys.modules["backend.app"])
    monkeypatch.setitem(sys.modules, "app.domain", sys.modules["backend.app.domain"])
    monkeypatch.setitem(sys.modules, "app.domain.models", sys.modules["backend.app.domain.models"])
    monkeypatch.setitem(sys.modules, "app.domain.review_models", sys.modules["backend.app.domain.review_models"])
    spec.loader.exec_module(module)
    engine = create_engine("sqlite://")
    tables = [t for t in Base.metadata.sorted_tables if t.name not in module.TABLES]
    Base.metadata.create_all(engine, tables=tables)
    with engine.begin() as connection:
        monkeypatch.setattr(module, "op", Operations(MigrationContext.configure(connection)))
        module.upgrade()
        assert set(module.TABLES) <= set(inspect(connection).get_table_names())
        trigger_names = {
            row[0]
            for row in connection.execute(
                text("SELECT name FROM sqlite_master WHERE type='trigger'")
            )
        }
        assert {
            f"guard_{name}_{action.lower()}"
            for name in module.IMMUTABLE
            for action in ("UPDATE", "DELETE")
        } <= trigger_names
        connection.execute(text("INSERT INTO review_event(id,case_id,sequence,actor_id,action,payload,previous_hash,digest,created_at) VALUES ('EV','C',1,'H','CREATED','{}','','x','2026-11-18')"))
        with pytest.raises(IntegrityError, match="append-only"):
            connection.execute(text("UPDATE review_event SET digest='changed' WHERE id='EV'"))
        with pytest.raises(IntegrityError, match="append-only"):
            connection.execute(text("DELETE FROM review_event WHERE id='EV'"))
        module.downgrade()
        assert not set(module.TABLES) & set(inspect(connection).get_table_names())
    engine.dispose()
