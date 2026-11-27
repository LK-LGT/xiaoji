"""P0-2：``ai_model_config`` + ``ai_prompt`` —— AI 配置不再只活在页面上。

Revision ID: 0047_ai_model_config
Revises: 0046_employee_mobile

背景
----
2026-11-26 复核发现 ``routes/v1/system_ai.py`` 的 10 个端点**全是空壳**：

- ``GET /configs`` 恒返回 ``[]``；
- ``POST /configs`` 返回一个 ``id=0`` 的对象，**库里什么都没写**；
- ``POST /configs/{id}/enable`` 同样只是回显，**全局开关没生效**；
- ``GET /prompts`` 恒返回 ``[]``。

⇒ 前端「AI 控制中心」的按钮全部是摆设：
管理员点「新增配置」看到「配置已新增」，刷新后**配置消失了**。

⚠️ 为什么现在建表
----------------
「AI 供应商选型」虽然待领导拍板（``docs/11`` §六 第 4 项），
但**配置管理本身**与选型无关 ——
先把「配置能存住、能启用、能查」这条链路做实，
供应商选定后只需在后台填一条配置即可生效。

⚠️ API Key 的处理
-----------------
``api_key_enc`` 存**加密后**的值（``services/secret_box`` 的信封加密），
**永不回传**给前端 —— 列表只暴露 ``apiKeyConfigured`` 布尔位。

⚠️ 不加「全局只能有一条 ENABLED」的数据库约束
----------------------------------------------
那条规则由**服务层**在启用时事务内保证（先关旧的再开新的），
数据库唯一约束做不到「软互斥」⇒ 放服务层更合适。
"""

import sqlalchemy as sa
from alembic import op

revision = "0047_ai_model_config"
down_revision = "0046_employee_mobile"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ai_model_config",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("config_name", sa.String(length=128), nullable=False),
        sa.Column("provider_code", sa.String(length=64), nullable=False, server_default="OPENAI_COMPATIBLE"),
        sa.Column("base_url", sa.String(length=512), nullable=False, server_default=""),
        sa.Column("api_key_enc", sa.String(length=1024), nullable=True),
        sa.Column("model_name", sa.String(length=128), nullable=False, server_default=""),
        sa.Column("timeout_seconds", sa.Integer(), nullable=False, server_default="60"),
        sa.Column("global_enabled", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("draft_enabled", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("optimize_enabled", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("check_enabled", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("allowed_user_ids", sa.String(length=1024), nullable=False, server_default=""),
        sa.Column("allowed_org_ids", sa.String(length=1024), nullable=False, server_default=""),
        sa.Column("draft_daily_limit", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("optimize_daily_limit", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("check_daily_limit", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("version_no", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="DISABLED"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_ai_model_config_status", "ai_model_config", ["status"])

    op.create_table(
        "ai_prompt",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("scene_code", sa.String(length=64), nullable=False),
        sa.Column("version_no", sa.String(length=64), nullable=False),
        sa.Column("system_prompt", sa.Text(), nullable=False, server_default=""),
        sa.Column("user_template", sa.Text(), nullable=False, server_default=""),
        sa.Column("output_schema_version", sa.String(length=32), nullable=False, server_default=""),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="PUBLISHED"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("scene_code", "version_no", name="uq_aiprompt_scene_version"),
    )
    op.create_index("ix_ai_prompt_scene_code", "ai_prompt", ["scene_code"])


def downgrade() -> None:
    op.drop_index("ix_ai_prompt_scene_code", table_name="ai_prompt")
    op.drop_table("ai_prompt")
    op.drop_index("ix_ai_model_config_status", table_name="ai_model_config")
    op.drop_table("ai_model_config")
