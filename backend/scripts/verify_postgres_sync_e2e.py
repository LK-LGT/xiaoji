"""PostgreSQL 真机验证脚本（R17 §8.3）。

用途
----
在**真实 PostgreSQL** 实例上跑一遍飞书同步全链路，确认：

1. **迁移可往返** —— 升级到 head → 降级 → 再升级，全程无损；
2. **schema 校验通过** —— 表、列、索引、唯一约束齐全；
3. **同步全链路可跑** —— 全量 → 增量 → 对账 → 幂等；
4. **VARCHAR 长度够用** —— PG **强制**长度，SQLite 不会报错的地方这里会；
5. **并发语义正确** —— 事件队列在多连接下不重复消费、不重复入队。

⚠️ 为什么需要单独脚本
---------------------
``backend/app/tests/test_postgres_readiness.py`` 固化的是
「SQLite 上测得出来的部分」（长度、索引名、唯一约束的存在性）。
剩下的是**只有在 PG 上才能验证的**：

- ``VARCHAR`` 长度**强制**（SQLite 不强制 —— 这正是 §8.3 的由来）；
- 迁移在 PG 上的实际行为（DDL 事务性、部分索引语法）；
- 并发下的真实竞态（PG 没有全局写锁）。

运行方式
--------
环境变量指定连接串（**不要**用生产库）::

    export DATABASE_URL="postgresql+psycopg://user:pass@localhost:5432/perf_verify"
    python backend/scripts/verify_postgres_sync_e2e.py

加 ``--dry-run`` 只做连接与 schema 检查，不改动数据。
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

PASS = "\033[92m✔\033[0m"
FAIL = "\033[91m✖\033[0m"
INFO = "\033[36mℹ\033[0m"

_results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    """记录一条检查结果。"""
    _results.append((name, ok, detail))
    mark = PASS if ok else FAIL
    print(f"  {mark} {name}" + (f" — {detail}" if detail else ""))
    return ok


def section(title: str) -> None:
    print(f"\n{title}")
    print("─" * 60)


def get_database_url() -> str:
    """取 PG 连接串。

    ⚠️ **必须显式指定** —— 不允许误连生产库。
    """
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        raise SystemExit(
            "❌ 未设置 DATABASE_URL。\n"
            '   示例: export DATABASE_URL="postgresql+psycopg://user:pass@host:5432/db"\n'
            "   ⚠️ 请勿指向生产库。"
        )
    if not url.startswith("postgresql"):
        raise SystemExit(
            f"❌ DATABASE_URL 必须是 PostgreSQL 连接串，当前为 {url.split('://')[0]}://"
        )
    if "perf" in url and "verify" not in url and "test" not in url:
        print(
            f"  {INFO} 数据库名看起来像生产库（{url.rsplit('/', 1)[-1]}）—— 请确认这是隔离环境"
        )
    return url


def main() -> int:
    parser = argparse.ArgumentParser(description="PostgreSQL 真机验证")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只做连接与 schema 检查，不跑同步、不改数据",
    )
    args = parser.parse_args()

    url = get_database_url()

    # ⚠️ 必须在导入 app 模块**之前**改环境变量 ——
    #    settings 在导入时就读了 DATABASE_URL。
    os.environ["DATABASE_URL"] = url

    section("0. 环境")
    check(
        "DATABASE_URL 指向 PostgreSQL",
        url.startswith("postgresql"),
        url.split("@")[-1],
    )

    from sqlalchemy import create_engine, text

    engine = create_engine(url, future=True)
    try:
        with engine.connect() as conn:
            version = conn.execute(text("SELECT version()")).scalar_one()
            check("连接成功", True, str(version).split(",")[0])
            dialect = conn.execute(text("SELECT current_database()")).scalar_one()
            check("当前数据库", True, str(dialect))
    except Exception as exc:
        check("连接成功", False, str(exc)[:200])
        return 1

    # ═══════════════════════════════════════════════════════════════════════
    section("1. 迁移往返")
    # ═══════════════════════════════════════════════════════════════════════
    from alembic import command
    from alembic.config import Config

    alembic_cfg = Config(str(ROOT / "backend" / "alembic.ini"))
    alembic_cfg.set_main_option("script_location", str(ROOT / "backend" / "migrations"))

    try:
        command.upgrade(alembic_cfg, "head")
        check("升级到 head", True)
    except Exception as exc:
        check("升级到 head", False, str(exc)[:300])
        return 1

    try:
        command.downgrade(alembic_cfg, "-1")
        command.upgrade(alembic_cfg, "head")
        check("降级再升级（往返无损）", True)
    except Exception as exc:
        check("降级再升级（往返无损）", False, str(exc)[:300])

    # ═══════════════════════════════════════════════════════════════════════
    section("2. Schema 校验")
    # ═══════════════════════════════════════════════════════════════════════
    from sqlalchemy import inspect as sa_inspect

    inspector = sa_inspect(engine)
    tables = set(inspector.get_table_names())

    expected_tables = {
        "organization_sync_batch",
        "organization_sync_cursor",
        "organization_sync_item_error",
        "organization_sync_audit",
        "organization_sync_alert",
        "feishu_identity",
        "feishu_identity_audit",
        "org_change_queue",
        "employee",
        "department",
    }
    missing = expected_tables - tables
    check("同步相关表齐全", not missing, f"缺: {sorted(missing)}" if missing else "")

    # 事件队列的列
    if "org_change_queue" in tables:
        cols = {c["name"] for c in inspector.get_columns("org_change_queue")}
        needed = {
            "id",
            "event_type",
            "resource_type",
            "resource_id",
            "status",
            "retry_count",
            "next_retry_at",
            "dedup_key",
            "payload_json",
            "batch_id",
            "error_message",
            "processed_at",
            "created_at",
        }
        check(
            "org_change_queue 列齐全",
            needed <= cols,
            f"缺: {sorted(needed - cols)}" if needed - cols else "",
        )

    # ⚠️ **部分唯一索引** —— PG 上并发去重的最后一道防线。
    if "org_change_queue" in tables:
        indexes = inspector.get_indexes("org_change_queue")
        partial = [
            idx
            for idx in indexes
            if idx.get("unique")
            and idx.get("column_names") == ["dedup_key"]
            and idx.get("dialect_options", {}).get("postgresql_where")
        ]
        check(
            "去重部分唯一索引存在（PostgreSQL where 子句）",
            bool(partial),
            partial[0]["name"] if partial else "未找到",
        )

    # 游标列长度 —— PG 强制，这里才会真报错
    if "organization_sync_cursor" in tables:
        cols = {c["name"]: c for c in inspector.get_columns("organization_sync_cursor")}
        rt = cols.get("resource_type")
        check(
            "organization_sync_cursor.resource_type 长度 >= 128",
            rt is not None and (rt.get("type") is None or rt["type"].length >= 128),
            f"实际 {rt.get('type').length if rt and rt.get('type') else '?'}",
        )

    # 幂等键唯一约束
    if "organization_sync_batch" in tables:
        constraints = inspector.get_unique_constraints("organization_sync_batch")
        cols = {tuple(sorted(c["column_names"])) for c in constraints}
        check(
            "organization_sync_batch.idempotency_key 唯一约束",
            ("idempotency_key",) in cols,
        )

    # 身份映射唯一约束
    if "feishu_identity" in tables:
        constraints = inspector.get_unique_constraints("feishu_identity")
        cols = {tuple(sorted(c["column_names"])) for c in constraints}
        check(
            "feishu_identity (identity_type, identity_value) 唯一约束",
            ("identity_type", "identity_value") in cols,
        )

    # ═══════════════════════════════════════════════════════════════════════
    section("3. 同步全链路")
    # ═══════════════════════════════════════════════════════════════════════
    if args.dry_run:
        print(f"  {INFO} --dry-run：跳过同步链路")
    else:
        run_sync_e2e(engine)

    # ═══════════════════════════════════════════════════════════════════════
    section("结果")
    # ═══════════════════════════════════════════════════════════════════════
    passed = sum(1 for _, ok, _ in _results if ok)
    failed = len(_results) - passed
    print(f"  {passed} 项通过，{failed} 项失败")

    if failed:
        print("\n  失败明细：")
        for name, ok, detail in _results:
            if not ok:
                print(f"    ✖ {name} — {detail}")
        return 1

    print("\n✅ PostgreSQL 真机验证全部通过")
    return 0


def run_sync_e2e(engine) -> None:
    """在 PG 上跑同步全链路（全量 → 增量 → 对账 → 幂等）。"""
    from sqlalchemy.orm import sessionmaker

    from backend.app.domain.models import Employee
    from backend.app.services.feishu_client import FeishuClient, MockTransport
    from backend.app.services.organization_sync import OrganizationSyncService

    db = sessionmaker(bind=engine, future=True)()

    def token_response():
        return {
            "status_code": 200,
            "json": {"code": 0, "tenant_access_token": "t", "expire": 7200},
        }

    def user_payload(uid: str, name: str):
        return {
            "user_id": uid,
            "open_id": f"ou_{uid}",
            "union_id": f"on_{uid}",
            "name": name,
            "email": f"{uid}@example.com",
            "department_ids": [],
            "leader_user_id": None,
            "status": {
                "is_activated": True,
                "is_exited": False,
                "is_frozen": False,
                "is_resigned": False,
            },
        }

    uid = "pg_e2e_user_001"

    def make_client():
        return FeishuClient(
            app_id="a",
            app_secret="s",
            transport=MockTransport(
                {
                    "POST /auth/v3/tenant_access_token/internal": [token_response()],
                    "GET /contact/v3/departments/0/children": [
                        {"status_code": 200, "json": {"code": 0, "data": {"items": []}}}
                    ],
                    "GET /contact/v3/users": [
                        {
                            "status_code": 200,
                            "json": {
                                "code": 0,
                                "data": {
                                    "items": [user_payload(uid, "PG验证员工")],
                                    "has_more": False,
                                },
                            },
                        }
                    ],
                }
            ),
            sleep=lambda _: None,
        )

    # ── 全量同步 ────────────────────────────────────────────────────
    try:
        service = OrganizationSyncService(db, make_client(), mode="full")
        result = service.sync_all()
        check(
            "全量同步成功",
            result.get("status") in ("ok", "partial_failed"),
            f"status={result.get('status')}",
        )
    except Exception as exc:
        check("全量同步成功", False, str(exc)[:300])
        db.close()
        return

    # ⚠️ **VARCHAR 长度真实验证** —— PG 强制，SQLite 不会报错。
    try:
        employee = db.get(Employee, uid)
        check("员工落库", employee is not None, uid)
        if employee is not None:
            check(
                "字段长度在 PG 限制内",
                len(employee.name) <= 128 and len(employee.feishu_open_id or "") <= 128,
            )
    except Exception as exc:
        check("员工落库", False, str(exc)[:200])

    # ── 身份映射 ────────────────────────────────────────────────────
    try:
        from backend.app.services.feishu_identity_service import FeishuIdentityService

        conflicts = FeishuIdentityService(db).detect_conflicts()
        check("身份映射无冲突", not conflicts, f"{len(conflicts)} 条")
    except Exception as exc:
        check("身份映射无冲突", False, str(exc)[:200])

    # ── 幂等：再跑一次，记录数不变 ──────────────────────────────────
    try:
        before = db.query(Employee).count()
        service2 = OrganizationSyncService(db, make_client(), mode="full")
        service2.sync_all()
        after = db.query(Employee).count()
        check("重复同步幂等（记录数不变）", before == after, f"{before} → {after}")
    except Exception as exc:
        check("重复同步幂等（记录数不变）", False, str(exc)[:200])

    # ── 增量同步 ────────────────────────────────────────────────────
    try:
        service3 = OrganizationSyncService(db, make_client(), mode="incremental")
        result3 = service3.sync_all()
        check(
            "增量同步成功",
            result3.get("status") in ("ok", "partial_failed"),
            f"status={result3.get('status')}",
        )
    except Exception as exc:
        check("增量同步成功", False, str(exc)[:300])

    # ═══════════════════════════════════════════════════════════════════
    section("4. 并发语义（PG 特有）")
    # ═══════════════════════════════════════════════════════════════════
    try:
        from backend.app.services.org_change_queue import (
            STATUS_PENDING,
            enqueue_event,
        )

        # 唯一约束：重复的未完成事件必须被数据库拒绝
        key = "pg_e2e_dedup_key"
        enqueue_event(db, "user_updated", "user", "pg_e2e_x", {}, dedup_key=key)
        from sqlalchemy.exc import IntegrityError

        from backend.app.domain.models import OrgChangeQueue as QueueModel

        db.add(
            QueueModel(
                event_type="user_updated",
                resource_type="user",
                resource_id="pg_e2e_x",
                status=STATUS_PENDING,
                dedup_key=key,
            )
        )
        try:
            db.commit()
            check("并发去重：唯一约束生效", False, "重复键竟然插入了")
        except IntegrityError:
            db.rollback()
            check("并发去重：唯一约束生效", True)

        # 部分索引语义：processed 不应挡住新事件
        from backend.app.services.org_change_queue import build_dedup_key

        k2 = build_dedup_key("user_updated", "user", "pg_e2e_y", event_id="pg-e2e-2")
        row, _ = enqueue_event(db, "user_updated", "user", "pg_e2e_y", {}, dedup_key=k2)
        if row is not None:
            row.status = "processed"
            db.commit()
        _, created = enqueue_event(
            db, "user_updated", "user", "pg_e2e_y", {}, dedup_key=k2
        )
        check("已处理事件不挡住下一次变更", created)

        # 并发领取：两会话同时领取，领到的必须不重叠
        from backend.app.services.org_change_queue import claim_pending_events

        for i in range(4):
            enqueue_event(
                db,
                "user_updated",
                "user",
                f"pg_conc_{i}",
                {},
                dedup_key=f"pg_conc_{i}",
            )
        db.commit()

        s2 = sessionmaker(bind=engine, future=True)()
        try:
            claimed_a = claim_pending_events(db)
            claimed_b = claim_pending_events(s2)
            ids_a = {r.id for r in claimed_a}
            ids_b = {r.id for r in claimed_b}
            check(
                "并发领取不重叠（FOR UPDATE SKIP LOCKED）",
                not (ids_a & ids_b),
                f"A={len(ids_a)} B={len(ids_b)} 交集={len(ids_a & ids_b)}",
            )
        finally:
            s2.close()
    except Exception as exc:
        check("并发语义验证", False, str(exc)[:300])

    # ── 清理 ────────────────────────────────────────────────────────
    try:
        db.query(QueueModel).delete()
        db.commit()
    except Exception:
        db.rollback()

    db.close()


if __name__ == "__main__":
    raise SystemExit(main())
