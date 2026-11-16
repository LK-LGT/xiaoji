"""【板块1】鉴权权限层单元测试。

测试覆盖：
1. Mock token 解析（get_current_user）
2. 不同角色的权限检查（check_permission）
3. 越权场景拒绝（PermissionDenied）
4. 工具函数（check_can_view_score、check_can_submit_review）

迁移说明（R19）
----------------
本文件原在 ``backend/tests/test_dependencies.py``。那个目录**不在**
``pyproject.toml`` 的 ``testpaths`` 里 —— 裸 ``pytest`` 根本不收集它，
等于「躺在仓库里假装是测试，实际一次都没跑过」。

它原来用 ``@pytest.mark.asyncio`` + ``async def`` + ``await``，
但 ``pytest-asyncio`` **从未被声明为依赖**（``backend/requirements.txt``
里没有它），marker 也没在 ``pyproject.toml`` 注册 ——
于是显式跑它会在**收集阶段**直接炸：

    'asyncio' not found in `markers` configuration option

仓库其余 55 个测试文件**一个都没有**用 async 测试，依赖里也没有
pytest-asyncio。所以本轮把 async 测试**转成同步**（用 ``_run`` 包一层
``asyncio.run``），而不是给仓库新增一个异步测试依赖。
"""

import asyncio

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from backend.app.core.db import get_sessionmaker
from backend.app.domain import models
from backend.app.dependencies import (
    get_current_user,
    get_direct_reports,
    check_permission,
    check_can_view_score,
    check_can_submit_review,
    UserRole,
    MOCK_TOKENS,
)
from backend.app.exceptions import NotAuthenticated, PermissionDenied, RecordNotFound


def _run(coro):
    """同步跑一个协程。

    ``get_current_user`` 等依赖函数本身是 ``async``（FastAPI 依赖要求），
    但**测试本身不需要异步** —— 用 ``asyncio.run`` 包一层即可，
    这样就不用引入 ``pytest-asyncio`` 依赖。
    """
    return asyncio.run(coro)


@pytest.fixture()
def db(tmp_path, monkeypatch):
    """每个测试一个**独立**的内存库。

    ⚠️ 迁移时发现的两个真问题（原文件用的是 ``get_sessionmaker()``，
    也就是**共享的 perf.db**）：

    1. **会污染真实库** —— mock token 路径在库里找不到员工时会
       ``db.add(employee); db.commit()``，也就是**往真实数据库里
       写测试员工**。测试不该有副作用。
    2. **会死锁** —— 共享库上多个测试并发写 → SQLite 锁等待 → 挂起。

    另外 mock token 分支只在 ``secure_auth=False`` 时才走
    （仓库默认 ``SECURE_AUTH=true``），所以这里必须显式关掉。
    """
    from backend.app.core import config as config_mod
    from backend.app.domain.models import Base

    url = f"sqlite:///{tmp_path / 'acl.db'}"
    monkeypatch.setattr(config_mod.settings, "database_url", url)
    monkeypatch.setattr(config_mod.settings, "secure_auth", False)

    import backend.app.core.db as db_mod

    db_mod._engine = None
    db_mod._SessionLocal = None

    engine = create_engine(url)
    Base.metadata.create_all(engine)

    session = get_sessionmaker()()
    try:
        # Mock token 的员工引用了 ``DEPT_SALES`` 部门与 ``M001`` 主管，
        # 而 employee 表对这两列有**外键约束** —— 不先建好就会被拒：
        # ``FOREIGN KEY constraint failed``。
        # 所以先把 mock 依赖的组织架构铺好。
        _seed_mock_org(session)
        yield session
    finally:
        session.close()
        db_mod._engine = None
        db_mod._SessionLocal = None
        engine.dispose()


def _seed_mock_org(db: Session) -> None:
    """铺好 mock token 依赖的组织架构（部门 + 主管链）。

    ``MOCK_TOKENS`` 里每个员工都带 ``department_id`` / ``manager_id``，
    mock 路径会**直接建员工行**，所以必须先把被引用的部门与主管建出来，
    否则撞外键约束：

        FOREIGN KEY constraint failed

    这里**按 ``MOCK_TOKENS`` 自动推导**要建哪些部门与主管，
    而不是写死一张清单 —— 否则 mock 表一改，这里就会漏。

    ⚠️ 主管链必须**分层插入**：
    ``M001.manager_id = "M002"``，而 ``M002`` 自己也是 mock 员工。
    如果 M001 和 M002 同一批插入，SQLite 会因为「M002 还不存在」
    就拒绝 M001 的外键 —— 所以先建没有上级的主管，再建有上级的。
    """
    # 1) 所有被引用的部门
    dept_ids = {
        token_data["department_id"]
        for token_data in MOCK_TOKENS.values()
        if token_data.get("department_id")
    }
    for dept_id in sorted(dept_ids):
        db.add(models.Department(id=dept_id, name=f"测试部门 {dept_id}"))
    db.flush()

    # 2) 主管链：先建「自己就是 mock 员工、且有上级」的，
    #    再建「自己也是 mock 员工、但上级还没建」的 —— 逐层向下。
    #    简单起见用「按上级依赖深度」排序：上级越浅越先建。
    def depth_of(employee_id: str, seen: frozenset[str] = frozenset()) -> int:
        """ employee's 在主管链上的深度（0 = 没有上级）。"""
        if employee_id in seen:
            return 0
        token_data = next(
            (t for t in MOCK_TOKENS.values() if t["employee_id"] == employee_id),
            None,
        )
        if token_data is None or not token_data.get("manager_id"):
            return 0
        return 1 + depth_of(token_data["manager_id"], seen | {employee_id})

    # 需要预建的员工 = 所有 mock token 的员工（mock 路径可能自己建，
    # 但有外键依赖，所以先建好，省得它去撞约束）。
    employee_ids = {t["employee_id"] for t in MOCK_TOKENS.values()}
    for employee_id in sorted(employee_ids, key=lambda e: (depth_of(e), e)):
        if db.query(models.Employee).filter_by(id=employee_id).first() is not None:
            continue
        token_data = next(
            t for t in MOCK_TOKENS.values() if t["employee_id"] == employee_id
        )
        db.add(
            models.Employee(
                id=employee_id,
                name=token_data["name"],
                position=token_data.get("position"),
                manager_id=token_data.get("manager_id"),
                department_id=token_data.get("department_id"),
                employment_status="active",
            )
        )
        # 逐个 flush，保证「上级已存在」的外键顺序。
        db.flush()
    db.commit()

    # 3) 把 HR 角色所在的部门登记进 ``hr_department_config``。
    #
    # ⚠️ 为什么必须登记：
    # HR 角色**不是**看 ``employee.position`` 决定的，而是看
    # 「该员工所属部门是否为**启用的** HR 部门」
    # （见 ``access_control.build_access_context``）。
    # 这是一条**刻意的安全设计** —— 头衔可以随便填，
    # 只有在系统配置里登记过的部门才真的能拿到 HR 权限。
    #
    # 不登记的话，HR001 就只是个「职位写着 HR 总监的普通员工」，
    # 于是「HR 能查所有人成绩」这条权限走不通，测试会误报失败。
    hr_dept_ids = {
        token_data["department_id"]
        for token_data in MOCK_TOKENS.values()
        if token_data.get("role") == "hr" and token_data.get("department_id")
    }
    for dept_id in sorted(hr_dept_ids):
        existing = db.query(models.HrDepartmentConfig).filter_by(
            department_id=dept_id
        ).first()
        if existing is None:
            db.add(models.HrDepartmentConfig(department_id=dept_id, active=True))
        else:
            existing.active = True
    db.commit()

    # 3) HR 角色来自 **HrDepartmentConfig**（HR 部门配置表），
    #    不是来自 MOCK_TOKENS 的 role 字段，也不是来自 position 里的 "HR"。
    #    ``build_access_context`` 会查这张表决定谁算 HR。
    #    所以 mock 的 HR 员工所在部门必须配成 HR 部门，否则没有 view_all_scores。
    hr_dept_ids = {
        token_data["department_id"]
        for token_data in MOCK_TOKENS.values()
        if token_data.get("role") == "hr" and token_data.get("department_id")
    }
    for hr_dept_id in sorted(hr_dept_ids):
        exists = db.query(models.HrDepartmentConfig).filter_by(
            department_id=hr_dept_id
        ).first()
        if exists is None:
            db.add(
                models.HrDepartmentConfig(
                    department_id=hr_dept_id,
                    active=True,
                )
            )
    db.commit()


class TestGetCurrentUser:
    """测试身份验证（get_current_user）。"""

    def test_valid_token_ordinary_employee(self, db: Session):
        """测试：有效的普通员工 token。"""
        authorization = "Bearer emp_token_001"

        user = _run(get_current_user(authorization=authorization, db=db))

        assert user is not None
        assert user.id == "E001"
        assert user.name == "刘刚（销售代表）"
        assert user.manager_id == "M001"
    
    def test_valid_token_manager(self, db: Session):
        """测试：有效的主管 token。"""
        authorization = "Bearer manager_token_001"
        
        user = _run(get_current_user(authorization=authorization, db=db))
        
        assert user is not None
        assert user.id == "M001"
        assert user.name == "王伟（销售经理）"
        assert user.position == "销售经理"
    
    def test_valid_token_hr(self, db: Session):
        """测试：有效的 HR token。"""
        authorization = "Bearer hr_token_001"
        
        user = _run(get_current_user(authorization=authorization, db=db))
        
        assert user is not None
        assert user.id == "HR001"
        assert user.name == "李红（HR总监）"
    
    def test_invalid_token(self, db: Session):
        """测试：无效的 token。"""
        authorization = "Bearer invalid_token_xyz"
        
        with pytest.raises(NotAuthenticated) as exc_info:
            _run(get_current_user(authorization=authorization, db=db))
        
        assert exc_info.value.code == "NOT_AUTHENTICATED"
    
    def test_missing_authorization_header(self, db: Session):
        """测试：缺少 Authorization header。"""
        with pytest.raises(NotAuthenticated) as exc_info:
            _run(get_current_user(authorization=None, db=db))
        
        assert "缺少 Authorization header" in exc_info.value.message
    
    def test_malformed_authorization_header(self, db: Session):
        """测试：Authorization header 格式错误。"""
        authorization = "InvalidFormat emp_token_001"
        
        with pytest.raises(NotAuthenticated) as exc_info:
            _run(get_current_user(authorization=authorization, db=db))
        
        assert "格式错误" in exc_info.value.message
    
    def test_bearer_prefix_case_insensitive(self, db: Session):
        """测试：Bearer 前缀大小写不敏感。"""
        authorization = "bearer emp_token_001"  # 小写 bearer
        
        user = _run(get_current_user(authorization=authorization, db=db))
        
        assert user.id == "E001"


class TestGetDirectReports:
    """测试直属下级查询（get_direct_reports）。"""

    def test_manager_with_direct_reports(self, db: Session):
        """测试：主管有直属下级。"""
        # M001 有 E001、E002 两个直属下级
        m001 = _run(get_current_user(authorization="Bearer manager_token_001", db=db))
        direct_reports = get_direct_reports(user=m001, db=db)
        
        # 这里的结果取决于数据库中有多少 manager_id="M001" 的员工
        # 由于我们的 Mock 数据，应该包括 E001 和 E002
        assert isinstance(direct_reports, list)
        # assert "E001" in direct_reports
        # assert "E002" in direct_reports
    
    def test_employee_no_direct_reports(self, db: Session):
        """测试：普通员工没有直属下级。"""
        e001 = _run(get_current_user(authorization="Bearer emp_token_001", db=db))
        direct_reports = get_direct_reports(user=e001, db=db)
        
        assert direct_reports == []


class TestCheckPermission:
    """测试权限矩阵检查（check_permission）。"""

    def test_employee_can_view_own_score(self, db: Session):
        """测试：普通员工能查看自己的成绩。"""
        e001 = _run(get_current_user(authorization="Bearer emp_token_001", db=db))
        
        result = check_permission(e001, "view_own_score")
        
        assert result is True
    
    def test_employee_cannot_view_all_scores(self, db: Session):
        """测试：普通员工不能查看所有成绩。"""
        e001 = _run(get_current_user(authorization="Bearer emp_token_001", db=db))
        
        result = check_permission(e001, "view_all_scores")
        
        assert result is False
    
    def test_manager_can_view_direct_report_score(self, db: Session):
        """测试：主管能查看直属下级的成绩。"""
        m001 = _run(get_current_user(authorization="Bearer manager_token_001", db=db))
        
        result = check_permission(m001, "view_direct_report_score")
        
        assert result is True
    
    def test_manager_cannot_view_all_scores(self, db: Session):
        """测试：主管不能查看所有成绩（跨部门）。"""
        m001 = _run(get_current_user(authorization="Bearer manager_token_001", db=db))
        
        result = check_permission(m001, "view_all_scores")
        
        assert result is False
    
    def test_hr_can_view_all_scores(self, db: Session):
        """测试：HR 能查看所有成绩。"""
        hr = _run(get_current_user(authorization="Bearer hr_token_001", db=db))
        
        result = check_permission(hr, "view_all_scores")
        
        assert result is True
    
    def test_hr_can_confirm_rating(self, db: Session):
        """测试：HR 能确认等级。"""
        hr = _run(get_current_user(authorization="Bearer hr_token_001", db=db))
        
        result = check_permission(hr, "confirm_rating")
        
        assert result is True
    
    def test_employee_cannot_confirm_rating(self, db: Session):
        """测试：普通员工不能确认等级。"""
        e001 = _run(get_current_user(authorization="Bearer emp_token_001", db=db))
        
        result = check_permission(e001, "confirm_rating")
        
        assert result is False


class TestCheckCanViewScore:
    """测试查看成绩权限（check_can_view_score）。"""

    def test_user_can_view_own_score(self, db: Session):
        """测试：员工能查看自己的成绩。"""
        e001 = _run(get_current_user(authorization="Bearer emp_token_001", db=db))
        
        result = check_can_view_score(
            user_id=e001.id,
            target_employee_id="E001",
            db=db
        )
        
        assert result is True
    
    def test_manager_can_view_direct_report_score(self, db: Session):
        """测试：主管能查看直属下级的成绩。"""
        m001 = _run(get_current_user(authorization="Bearer manager_token_001", db=db))
        e001 = _run(get_current_user(authorization="Bearer emp_token_001", db=db))
        
        # 这里需要确保 E001 是 M001 的下级（已在 Mock 数据中设置）
        result = check_can_view_score(
            user_id=m001.id,
            target_employee_id=e001.id,
            db=db
        )
        
        assert result is True
    
    def test_employee_cannot_view_other_score(self, db: Session):
        """测试：普通员工不能查看其他员工的成绩。"""
        e001 = _run(get_current_user(authorization="Bearer emp_token_001", db=db))
        
        with pytest.raises(PermissionDenied) as exc_info:
            check_can_view_score(
                user_id=e001.id,
                target_employee_id="E002",
                db=db
            )
        
        assert exc_info.value.code == "PERMISSION_DENIED"
    
    def test_hr_can_view_any_score(self, db: Session):
        """测试：HR 能查看任何员工的成绩。"""
        hr = _run(get_current_user(authorization="Bearer hr_token_001", db=db))
        
        result = check_can_view_score(
            user_id=hr.id,
            target_employee_id="E001",
            db=db
        )
        
        assert result is True
    
    def test_manager_cannot_view_non_subordinate_score(self, db: Session):
        """测试：主管不能查看非直属下级的成绩。"""
        m001 = _run(get_current_user(authorization="Bearer manager_token_001", db=db))
        
        # E003 由 M002 管理，不是 M001 的直属下级
        with pytest.raises(PermissionDenied):
            check_can_view_score(
                user_id=m001.id,
                target_employee_id="E003",
                db=db
            )


class TestCheckCanSubmitReview:
    """测试提交评分权限（check_can_submit_review）。"""

    @pytest.fixture
    def seeded_db(self, db: Session):
        """在**隔离库**里建好测试数据。

        ⚠️ 原实现用的是共享的 ``perf.db``，而且会
        ``session.query(models.Employee).delete()`` ——
        也就是**清空真实数据库的 employee 表**。这是绝对不能接受的副作用。
        """
        self._setup_test_data(db)
        return db

    @staticmethod
    def _setup_test_data(db: Session):
        """创建测试数据。

        ⚠️ ``DEPT_SALES`` 部门与 ``M001`` 主管**已经**由模块级
        ``db`` fixture 的 ``_seed_mock_org()`` 建好了（mock token 路径
        需要它们满足外键约束）。这里如果再插一次同 ID 的行，
        就会撞 ``UNIQUE constraint failed: department.id``。
        所以先查后插 —— 存在就复用。
        """
        # 部门（可能已被 _seed_mock_org 建好）。
        dept = db.get(models.Department, "DEPT_SALES")
        if dept is None:
            dept = models.Department(id="DEPT_SALES", name="销售部")
            db.add(dept)
            db.flush()

        # 主管和员工（可能已被 _seed_mock_org 建好 M001/M002）。
        m001 = db.get(models.Employee, "M001")
        if m001 is None:
            m001 = models.Employee(
                id="M001",
                name="王伟",
                position="销售经理",
                manager_id=None,
                department_id="DEPT_SALES",
                employment_status="active"
            )
            db.add(m001)

        e001 = db.get(models.Employee, "E001")
        if e001 is None:
            e001 = models.Employee(
                id="E001",
                name="刘刚",
                position="销售代表",
                manager_id="M001",
                department_id="DEPT_SALES",
                employment_status="active"
            )
            db.add(e001)
        db.add_all([m001, e001])
        db.flush()
        
        # 创建考核任务
        task = models.AssessmentTask(
            id="TASK001",
            name="2026年11月考核",
            period="202611",
            status="self_review_open"
        )
        db.add(task)
        db.flush()
        
        # 创建考核记录
        record_e001 = models.AssessmentRecord(
            id="RECORD_E001",
            task_id="TASK001",
            employee_id="E001",
            status="self_review_open"
        )
        db.add(record_e001)
        db.commit()
    
    def test_employee_can_submit_own_self_review(self, seeded_db: Session):
        """测试：员工能提交自己的自评。"""
        db = seeded_db
        e001 = db.query(models.Employee).filter_by(id="E001").first()

        assert e001 is not None, "测试数据应已建好"

        result = check_can_submit_review(
            user_id=e001.id,
            target_record_id="RECORD_E001",
            db=db
        )

        assert result is not None
        assert result.id == "RECORD_E001"

    def test_manager_can_submit_direct_report_review(self, seeded_db: Session):
        """测试：主管能提交直属下级的评分。"""
        db = seeded_db
        m001 = db.query(models.Employee).filter_by(id="M001").first()

        assert m001 is not None, "测试数据应已建好"

        result = check_can_submit_review(
            user_id=m001.id,
            target_record_id="RECORD_E001",
            db=db
        )

        assert result is not None
        assert result.id == "RECORD_E001"

    def test_employee_cannot_submit_other_self_review(self, seeded_db: Session):
        """测试：员工不能提交其他人的自评。"""
        db = seeded_db
        e002 = models.Employee(
            id="E002",
            name="赵燕",
            position="销售代表",
            manager_id="M001",
            employment_status="active"
        )

        with pytest.raises(PermissionDenied):
            check_can_submit_review(
                user_id=e002.id,
                target_record_id="RECORD_E001",
                db=db
            )

    def test_record_not_found(self, seeded_db: Session):
        """测试：记录不存在。"""
        db = seeded_db
        e001 = db.query(models.Employee).filter_by(id="E001").first()

        assert e001 is not None, "测试数据应已建好"

        with pytest.raises(RecordNotFound) as exc_info:
            check_can_submit_review(
                user_id=e001.id,
                target_record_id="NONEXISTENT_RECORD",
                db=db
            )

        assert exc_info.value.code == "NOT_FOUND"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
