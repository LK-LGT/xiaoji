"""0017: 密码哈希字段（R4 —— 修掉「全员明文 123456」）

Revision ID: 0017_employee_password_hash
Revises: 0016_align_orm_unique_constraints
Create Date: 2026-11-16

背景
----
盘点项 R4：``backend/app/routes/auth.py`` 的登录逻辑是

    if password != "123456":
        raise HTTPException(401)

——**全员共用一个明文口令**，等于没设密码。任何人拿到工号就能登录
任何人的账号。而 ``/v1/auth/change-password`` 是**空实现**（直接返回成功），
改密根本不存在。

本迁移给 ``employee`` 表补三列：

    password_hash          TEXT     密码哈希（PBKDF2-HMAC-SHA256，自描述格式）
    password_set_at        DATETIME 最近一次设置密码的时间
    force_change_password  BOOLEAN  是否强制下次登录后改密

渐进迁移策略
------------
历史数据**没有**哈希值（`password_hash IS NULL`）。本迁移**不**批量
回填哈希 —— 因为回填必须用各账号的真实密码来算，而系统里根本没存密码，
统一口令 ``123456`` 虽然已知，但把它哈希进去等于**把弱口令固化**，
反而制造「看起来已经加密了」的错觉。

所以策略是：

1. 迁移只**加列**，不动任何数据；
2. 登录时若命中兼容明文 ``123456``（且该账号尚无哈希），
   **登录成功后立即补写哈希**并把 ``force_change_password`` 置 True
   → 用户下次登录后必须改密，且弱口令从此不再可用；
3. 账号若既无哈希、密码也不匹配 → 一律 401。

这样**不停机、不锁死任何在职用户**地完成迁移。
真正的强口令由用户在改密流程里设置。

幂等
----
``op.add_column`` 前先检查列是否已存在，可重复执行。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0017_employee_password_hash"
down_revision: str | Sequence[str] | None = "0016_align_orm_unique_constraints"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "employee"


def _inspector():
    return sa.inspect(op.get_bind())


def _existing_columns() -> set[str]:
    try:
        return {c["name"] for c in _inspector().get_columns(TABLE)}
    except Exception:
        return set()


def upgrade() -> None:
    existing = _existing_columns()
    if not existing:
        # 表不存在（全新库由 0001 建），直接跳过。
        print("[0017] employee 表不存在，跳过")
        return

    if "password_hash" not in existing:
        op.add_column(
            TABLE,
            sa.Column("password_hash", sa.String(length=255), nullable=True),
        )
        print("[0017] 已加 employee.password_hash")

    if "password_set_at" not in existing:
        op.add_column(
            TABLE,
            sa.Column("password_set_at", sa.DateTime(), nullable=True),
        )
        print("[0017] 已加 employee.password_set_at")

    if "force_change_password" not in existing:
        op.add_column(
            TABLE,
            sa.Column(
                "force_change_password",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("0"),
            ),
        )
        print("[0017] 已加 employee.force_change_password")


def downgrade() -> None:
    existing = _existing_columns()
    for col in ("force_change_password", "password_set_at", "password_hash"):
        if col in existing:
            with op.batch_alter_table(TABLE, schema=None) as batch_op:
                batch_op.drop_column(col)
            print(f"[0017] 已移除 employee.{col}")
