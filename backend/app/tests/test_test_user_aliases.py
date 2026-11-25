"""🔴 **测试账号别名 → 真实身份**（2026-11-24，B2）。

背景
----
测试账号过去叫 ``admin`` / ``manager`` / ``leader`` / ``employee``。
⚠️ **为什么 ``manager`` / ``leader`` 这组名字有问题**

① **两个不同的人，权限完全一样**
   实测 ``manager``（杨飞）与 ``leader``（别嘧咪）**都是总经理 / 总经办**，
   ``access_control`` 给的角色集与权限集**逐项相同（零差异）**
   ⇒ 写「主管权限」还是「直属领导权限」的用例**根本测不出差别**，
   看似覆盖了两个角色，实际只覆盖了一个。

② **登录名与 RBAC 角色名撞车**
   RBAC 体系里的 ``manager`` 角色 = 「有直属下属」，
   ``department_leader`` = 「部门 ``leader_id``」。
   用同名做**登录名** ⇒ 看到 ``manager`` 这个字符串，
   根本分不清说的是**登录账号**还是**权限角色**。

③ **语义就是错的**
   ``leader`` 的旧注释写「直属领导」，但此人岗位是**总经理**，
   ``manager_id`` 为空 —— **不是任何人的直属上级**。

本文件锁住改名后的状态，并**显式验证**
「两个人虽然角色相同，但管辖范围确实不同」—— 这才是这两个账号的真实价值。
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from backend.app.routes import auth as auth_routes

# ═══════════════════════════════════════════════════════════════════════════
# 1. 别名 → 真实身份的映射
# ═══════════════════════════════════════════════════════════════════════════

#: 别名 → (员工 ID, 真实姓名)
#:
#: ⚠️ **必须与 ``auth.py::_TEST_USER_META`` 一致** —— 这是本文件的核心断言。
EXPECTED_IDENTITIES = {
    "admin": ("b8663c7c", "林雅静"),
    "yangfei": ("g426c684", "杨飞"),
    "biyumi": ("939931ba", "别嘧咪"),
    "employee": ("2aaf32ea", "李凯"),
}

#: ⚠️ **旧别名** —— 已经**不允许**再出现。
#:
#: 为什么要锁：改名之后若有人「顺手」把 ``manager`` 加回去，
#: 就会出现两个同义账号（``manager`` 与 ``yangfei`` 指向同一人）
#: ⇒ 又是「两个名字一个权限」，正是 B2 要消除的问题。
RETIRED_ALIASES = {"manager", "leader"}


class TestAliasIdentityMapping:
    """🔴 别名必须**唯一且指向真实身份**。"""

    def test_别名集合正确(self):
        """⚠️ **核心**：四个别名，一个不多一个不少。"""
        assert set(auth_routes._TEST_USER_META) == set(EXPECTED_IDENTITIES)

    def test_每个别名指向正确的员工(self):
        """⚠️ **核心**：别名 → 员工 ID 的映射不能错位。

        ⚠️ **为什么这条最重要**
        别名错了 ⇒ 以为在测「总经理」，实际登录的是「员工」
        ⇒ 用例**静默失真**，还不报错。
        """
        for alias, (emp_id, name) in EXPECTED_IDENTITIES.items():
            info = auth_routes._TEST_USER_META[alias]
            assert info["id"] == emp_id, (
                f"别名 {alias!r} 映射到 {info['id']}，期望 {emp_id}（{name}）"
            )
            assert info["name"] == name, (
                f"别名 {alias!r} 的显示名是 {info['name']!r}，期望 {name!r}"
            )

    def test_旧别名不得复活(self):
        """🔴 **核心**：``manager`` / ``leader`` 不得作为**登录名**再出现。

        ⚠️ **注意区分**
        RBAC **角色名**里的 ``manager`` / ``department_leader`` 是**另一套词**
        （``ROLE_UI_PERMISSIONS`` / ``access_control``）⇒ 那些**不受本约束**。
        本条只针对**测试账号登录名**（``_TEST_USER_META`` 的 key）。
        """
        assert not (RETIRED_ALIASES & set(auth_routes._TEST_USER_META)), (
            f"旧别名复活了：{RETIRED_ALIASES & set(auth_routes._TEST_USER_META)}"
        )

    def test_登录名不得与RBAC角色名撞车(self):
        """🔴 **核心**：登录名与 RBAC 角色名**不得同名**。

        ⚠️ **为什么这条是自动化的**
        ``manager`` / ``department_leader`` / ``hr`` / ``admin`` / ``employee``
        都是**运行时角色名**。若某个登录名恰好也在这张表里
        ⇒ 看到日志里的 ``manager`` 就分不清是「账号」还是「角色」
        ⇒ 这正是 B2 要消除的歧义。
        """
        from backend.app.routes.auth import ROLE_UI_PERMISSIONS

        # ⚠️ ``admin`` / ``employee`` 是**故意保留**的例外 ——
        #    它们既是登录名，也是角色名，但两者**指向同一事实**：
        #    ``admin`` 映射的林雅静确实在 ``system_admin`` 表里；
        #    ``employee`` 映射的李凯角色**只有** employee 一个。
        #    ⇒ 这种「同名同义」不产生歧义。
        allowed_overlap = {"admin", "employee"}

        for alias in auth_routes._TEST_USER_META:
            if alias in allowed_overlap:
                continue
            assert alias not in ROLE_UI_PERMISSIONS, (
                f"登录名 {alias!r} 与 RBAC 角色名撞车 ⇒ 日志里分不清是账号还是角色"
            )

    def test_环境变量口令的key与别名一致(self):
        """🔴 **核心**：根 conftest 注入的 JSON key 必须**正好**是这四个别名。

        ⚠️ **为什么这条容易漏**
        别名改了但 conftest 没改 ⇒ ``_build_test_users`` 会打
        「测试账号 xxx 没有口令，跳过」warning ⇒ **静默**少一个账号
        ⇒ 到某个用例才炸，且报错信息完全不指向根因。
        """
        raw = os.environ.get(auth_routes._TEST_USER_PLAINTEXT_ENV)
        assert raw, f"未注入 {auth_routes._TEST_USER_PLAINTEXT_ENV}"
        parsed = json.loads(raw)

        assert set(parsed) == set(EXPECTED_IDENTITIES), (
            f"口令 JSON 的 key 与别名不一致："
            f"注入={sorted(parsed)}，期望={sorted(EXPECTED_IDENTITIES)}"
        )

    def test_每个别名的口令都验得过(self):
        """⚠️ 每个别名都必须能真的登录（哈希比对通过）。"""
        from backend.app.services import password_hasher

        plaintexts = json.loads(os.environ[auth_routes._TEST_USER_PLAINTEXT_ENV])
        for alias, info in auth_routes.TEST_USERS.items():
            assert password_hasher.verify_password(
                plaintexts[alias], info["password_hash"]
            ), f"{alias} 的口令验不过 ⇒ 登录会 401"


# ═══════════════════════════════════════════════════════════════════════════
# 2. 「两个总经理」的价值：角色相同但管辖范围不同
# ═══════════════════════════════════════════════════════════════════════════


class TestTwoManagersAreDistinguishable:
    """🔴 别名改了不代表问题解决了 —— 必须验证两人**确实可区分**。

    ⚠️ **为什么这条是 B2 的验收点**
    B2 的起因是「``manager`` 与 ``leader`` 权限完全一样，测不出差别」。
    ⇒ 光改名**不解决问题** —— 必须确认这两人在**管辖范围**上确实不同
    ⇒ 「能不能看到某个人」这类用例才有区分度。

    ⚠️⚠️ **为什么这里用「自建数据」而不是查真实库**
    本仓库的 pytest **不保证**连着真实生产库 ——
    CI 用的是 ``data/_ci_local.db``（空库），
    开发者本地才是真库。
    ⇒ 若在这里查真实员工，CI 上必然失败（``查不到这两位员工``），
       本地绿 / CI 红 —— 最坏的一类失败。
    ⇒ **单元测试自建数据**（可复现）；
       **真实库的比对**放在 ``scripts/probe_test_user_scope.py``（人工跑）。
    """

    @pytest.fixture()
    def org(self):
        """造一个「两个总经理 + 一个员工」的小组织。

        ⇒ 结构刻意模拟真实数据里杨飞/别嘧咪的关系：
          两人都是部门 leader（⇒ 都有 ``department_leader``），
          两人都有直属下属（⇒ 都有 ``manager``）
          ⇒ **角色集相同**；
          但管的部门数不同（⇒ **管辖范围不同**）。
        """
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker
        from sqlalchemy.pool import StaticPool

        from backend.app.domain.models import Base, Department, Employee

        engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(engine)
        session = sessionmaker(bind=engine, autoflush=False, future=True)()

        # ⚠️ 杨飞：管 2 个部门，有 2 个直属下属
        session.add(Department(id="D_A", name="A部", leader_id="E_YANG", active=True))
        session.add(Department(id="D_A2", name="A二部", leader_id="E_YANG", active=True))
        # ⚠️ 别嘧咪：管 1 个部门，有 1 个直属下属
        session.add(Department(id="D_B", name="B部", leader_id="E_BIE", active=True))

        session.add_all(
            [
                Employee(
                    id="E_YANG", name="杨飞", department_id="D_A",
                    manager_id=None, employment_status="active",
                    account_status="enabled", token_version=1,
                ),
                Employee(
                    id="E_BIE", name="别嘧咪", department_id="D_B",
                    manager_id=None, employment_status="active",
                    account_status="enabled", token_version=1,
                ),
                # ⚠️ 李凯在杨飞线下 ⇒ 验证「员工确实在主管汇报线里」
                Employee(
                    id="E_LI", name="李凯", department_id="D_A",
                    manager_id="E_YANG", employment_status="active",
                    account_status="enabled", token_version=1,
                ),
                Employee(
                    id="E_BIE_REP", name="别嘧咪的下属", department_id="D_B",
                    manager_id="E_BIE", employment_status="active",
                    account_status="enabled", token_version=1,
                ),
            ]
        )
        session.commit()
        try:
            yield session
        finally:
            session.close()
            engine.dispose()

    def test_角色集相同但管辖范围不同(self, org):
        """⚠️ **核心验收点**。

        ⇒ 角色集相同是**数据事实**（都是总经理），
          但 ``managed_department_ids`` / ``reporting_line_ids`` 必须不同
          ⇒ 否则这两个账号仍是「两个名字一个权限」。
        """
        from backend.app.domain.models import Employee
        from backend.app.services.access_control import build_access_context

        yang = org.get(Employee, "E_YANG")
        bie = org.get(Employee, "E_BIE")
        ctx_yang = build_access_context(org, yang)
        ctx_bie = build_access_context(org, bie)

        # ⚠️ 角色集相同 —— 这正是 B2 要解决的「测不出差别」的根源
        assert ctx_yang.roles == ctx_bie.roles, (
            "本用例的前提是两人角色集相同（都是总经理）"
        )
        assert "manager" in ctx_yang.roles
        assert "department_leader" in ctx_yang.roles

        # ⚠️ **但管辖范围必须不同** —— 这才是两个账号的价值
        assert ctx_yang.managed_department_ids != ctx_bie.managed_department_ids, (
            "两人的管理部门完全相同 ⇒ 两个测试账号依然无法区分"
        )
        assert ctx_yang.reporting_line_ids != ctx_bie.reporting_line_ids, (
            "两人的汇报线完全相同 ⇒ 两个测试账号依然无法区分"
        )

        # ⚠️ 且范围大小不同（杨飞管 2 个部门、别嘧咪管 1 个）
        assert len(ctx_yang.managed_department_ids) > len(ctx_bie.managed_department_ids)

    def test_员工在主管的汇报线里(self, org):
        """⚠️ **员工账号必须真的在主管的汇报线里**。

        ⇒ 否则「员工被主管看到」这类用例根本走不通，
          改名后容易误以为测试覆盖变了。
        """
        from backend.app.domain.models import Employee
        from backend.app.services.access_control import build_access_context

        emp = org.get(Employee, "E_LI")
        assert emp.manager_id == "E_YANG"

        yang = org.get(Employee, "E_YANG")
        ctx_yang = build_access_context(org, yang)
        assert "E_LI" in ctx_yang.direct_report_ids
        assert "E_LI" in ctx_yang.reporting_line_ids

    def test_权限集相同但可见范围不同(self, org):
        """⚠️ **这才是「两个总经理」在业务上的真正区别**。

        ⇒ 权限**动作**一样（都是主管），
          但 ``can_access_employee`` 的**判定结果**不同
          ⇒ 「能不能看到某个人」这类用例**真的测得出差别**。
        """
        from backend.app.domain.models import Employee
        from backend.app.services.access_control import (
            build_access_context,
            can_access_employee,
        )

        yang = org.get(Employee, "E_YANG")
        bie = org.get(Employee, "E_BIE")
        li = org.get(Employee, "E_LI")
        bie_rep = org.get(Employee, "E_BIE_REP")

        ctx_yang = build_access_context(org, yang)
        ctx_bie = build_access_context(org, bie)

        # ⚠️ 李凯是杨飞的下属 ⇒ 杨飞能看到，别嘧咪**看不到**
        assert can_access_employee(ctx_yang, li, org) is True
        assert can_access_employee(ctx_bie, li, org) is False

        # ⚠️ 反过来对调 ⇒ 证明这不是「谁权限大」而是「各管各的」
        assert can_access_employee(ctx_bie, bie_rep, org) is True
        assert can_access_employee(ctx_yang, bie_rep, org) is False


# ═══════════════════════════════════════════════════════════════════════════
# 3. 别名不得泄漏到前端
# ═══════════════════════════════════════════════════════════════════════════


class TestNoAliasLeakToFrontend:
    """🔴 前端**不得**出现这些登录别名。"""

    def test_前端源码不含登录别名(self):
        """⚠️ 别名是**后端内部概念** ⇒ 前端出现就是误用。"""
        frontend = (
            Path(auth_routes.__file__).resolve().parents[3] / "frontend" / "src"
        )
        if not frontend.is_dir():
            pytest.skip("找不到 frontend/src")

        offenders: list[str] = []
        for path in frontend.rglob("*"):
            if path.suffix not in {".ts", ".vue", ".js"}:
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            for alias in ("yangfei", "biyumi"):
                if alias in text:
                    offenders.append(f"{path.relative_to(frontend)}: {alias}")

        assert not offenders, "前端出现测试账号别名：" + ", ".join(offenders)
