"""★ **工作量数据范围**的共享判定（2026-11-25 抽出）。

⚠️ **为什么要抽出来**
--------------------
``/v1/baidu/workload``（剪辑板块）和 ``/v1/dept/workload``（部门板块）
**都要用同一套数据范围**。

⚠️ **两份实现 ⇒ 迟早漂移**
    之前范围判定只写在 ``pan_workload.py`` 的私有函数里。
    新增通用板块时若**照抄一遍**，改了一处忘了另一处
    ⇒ **权限漏洞**（且极难发现：测试都过，只是行为不一致）。
    ⇒ 抽成服务层，**两个 router 都调它**。

⚠️ **口径（2026-11-24 业务确认）**
----------------------------------
- **HR** ⇒ 不限（可跨部门看绩效数据）
- **系统管理员** ⇒ **按真实身份**判定，**不因是管理员就放行**
  （管理员是真实员工账号，只是被授予了【绩效配置管理员】权限）
- **普通员工** ⇒ 只看自己
- **直属上级 / 汇报线全链下属** ⇒ 看得到
- **部门负责人** ⇒ 看所管部门（含下级部门）全员
- **代理授权** ⇒ 看得到被授权范围
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domain.models import Employee


def scope_employee_ids(db: Session, user: Employee) -> list[str] | None:
    """算出**当前用户能看谁的数据**。

    Returns:
        ``None`` = 不限（**仅 HR**）；
        列表 = 只能看这些人。

    ⚠️ **员工只看自己** —— 绝不能让人看到同事的数据。

    ⚠️🔴 **系统管理员不在「不限」口径内**（2026-11-24）
    管理员是**真实员工账号**，只是被授予了【绩效配置管理员】权限
    ⇒ 它的**数据范围**按真实身份（部门/汇报线）判定，
    不是「因为是管理员所以全放行」。
    管理动作权限（配置板块、维护绑定）不受影响。

    ⚠️⚠️ **必须含「直属下属 + 汇报线全链下属」**
    真实组织（2026-11-21 查库）::

        运营中心 (leader=杨飞)
            └── 短视频 (leader=刘旭)
                ├── 张洁  编导   ← ★ 两位剪辑的**直属上级**
                │   ├── 李婵雨 剪辑
                │   └── 佟瑶   剪辑

    张洁**不是**部门 leader（leader 是刘旭）⇒ 只查
    ``managed_department_ids`` ⇒ 她**看不到自己的下属** ⇒
    「主管核对下属工作量」这条主链路**根本走不通**。

    ⇒ 这里必须并上 ``direct_report_ids`` 与 ``reporting_line_ids``
      （汇报线全链闭包，主管要能查到「下属的下属」）。
    """
    from .access_control import build_access_context

    ctx = build_access_context(db, user)

    # 🔴 **2026-11-24 规则修订：只有 HR 能跨部门看数据**。
    #
    #    ⚠️ **为什么不再对 ``admin`` 放行**
    #    过去这里是 ``{"hr", "admin"} & set(ctx.roles)`` —— 把「系统管理员」
    #    和「HR」当成一个口径 ⇒ 管理员被白送了**跨部门业务数据范围**。
    #    实测（2026-11-24）：管理员账号登录后调端点，返回**全公司所有剪辑**
    #    的数据，而该管理员在人事行政部，与短视频部**无任何汇报线关系**。
    #
    #    ⇒ 业务规则：「管理员只是分配了【绩效配置管理员】权限的真实员工账号，
    #      不是万能模拟账号」⇒ 管理员的**数据范围**仍按其真实身份判定。
    #    ⇒ 管理员要管理动作权限（配置/同步）不受影响。
    #
    #    ⚠️ HR 保留跨部门 —— 业务口径确认（2026-11-24）。
    if "hr" in ctx.roles:
        return None

    allowed: set[str] = {user.id}

    # ⚠️ ① 直属下属 + 汇报线全链下属
    allowed.update(getattr(ctx, "direct_report_ids", None) or set())
    allowed.update(getattr(ctx, "reporting_line_ids", None) or set())

    # ⚠️ ② 部门负责人：所管部门（含下级部门）全员
    managed = set(getattr(ctx, "managed_department_ids", None) or [])
    if "department_leader" in ctx.roles and managed:
        rows = db.scalars(
            select(Employee.id).where(Employee.department_id.in_(managed))
        ).all()
        allowed.update(rows)

    # ⚠️ ③ 代理授权范围
    allowed.update(getattr(ctx, "delegated_employee_ids", None) or set())

    # ⚠️ **普通员工 ⇒ 只看自己**
    #   上面加的都是「有下属/有管理权」才有的集合 ⇒ 无管理权者为空。
    if allowed == {user.id}:
        return [user.id]
    return sorted(allowed)
