"""P0-2：``employee.mobile`` —— 本地建号需要存手机号。

Revision ID: 0046_employee_mobile
Revises: 0045_ai_tool_call_subject

背景
----
2026-11-26 修复「员工注册/更新/停用」三个假成功端点时，
发现 ``employee`` 表**没有手机号列**：

- 前端 ``EmployeeRegister.vue`` 要求管理员填手机号（11 位）；
- 后端原空壳实现把 ``mobile`` 直接丢掉；
- ``system_views.build_user_item`` 的 ``mobile`` 恒为 ``""``。

⇒ 真要「注册成功」，这一列必须存在，否则填的手机号无处可存。

⚠️ **允许为空**
存量员工（飞书同步来的）本地库里不一定留了手机号
⇒ ``nullable=True``，**不回填**（没有可信来源，凭空造数据比空着更糟）。

⚠️ **不加唯一约束**
手机号在真实组织里**可能重复**（比如共用号、外包同号）
⇒ 加唯一约束会在同步时炸库。
⇒ 去重职责放在**登录账号**（``login_account``，已有唯一约束）上。
"""

import sqlalchemy as sa
from alembic import op

revision = "0046_employee_mobile"
down_revision = "0045_ai_tool_call_subject"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "employee",
        sa.Column("mobile", sa.String(length=32), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("employee", "mobile")
