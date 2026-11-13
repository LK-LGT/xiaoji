"""飞书组织架构同步服务。

职责：
1. 从飞书拉部门、员工、主管关系
2. 保存到本地 DB
3. 处理特殊情况（离职、转部门等）
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.orm import Session

from app.domain.models import Department, Employee
from app.services.feishu_client import FeishuClient

logger = logging.getLogger(__name__)


class OrganizationSyncService:
    """组织架构同步服务。"""

    def __init__(self, db: Session, feishu_client: FeishuClient):
        self.db = db
        self.feishu_client = feishu_client

    def sync_all(self) -> dict[str, int]:
        """完整同步：部门 + 员工。

        Returns:
            统计信息 {"departments_synced": int, "employees_synced": int, "errors": int}
        """
        logger.info("Starting full organization sync from Feishu")

        stats = {
            "departments_synced": 0,
            "employees_synced": 0,
            "errors": 0,
        }

        try:
            # 1. 同步部门
            stats["departments_synced"] = self._sync_departments()

            # 2. 同步员工
            stats["employees_synced"] = self._sync_employees()

            logger.info(f"Organization sync completed: {stats}")
        except Exception as e:
            logger.exception(f"Organization sync failed: {e}")
            stats["errors"] = 1

        return stats

    def _sync_departments(self) -> int:
        """同步部门。

        Returns:
            同步的部门数
        """
        logger.debug("Syncing departments...")

        try:
            departments = self.feishu_client.fetch_departments()
        except Exception as e:
            logger.error(f"Failed to fetch departments: {e}")
            return 0

        count = 0
        for dept_data in departments:
            try:
                self._upsert_department(dept_data)
                count += 1
            except Exception as e:
                logger.warning(f"Failed to sync department {dept_data.get('department_id')}: {e}")

        self.db.commit()
        logger.info(f"Synced {count} departments")
        return count

    def _sync_employees(self) -> int:
        """同步员工。

        Returns:
            同步的员工数
        """
        logger.debug("Syncing employees...")

        try:
            employees = self.feishu_client.fetch_employees()
        except Exception as e:
            logger.error(f"Failed to fetch employees: {e}")
            return 0

        count = 0
        for emp_data in employees:
            try:
                self._upsert_employee(emp_data)
                count += 1
            except Exception as e:
                logger.warning(f"Failed to sync employee {emp_data.get('user_id')}: {e}")

        self.db.commit()
        logger.info(f"Synced {count} employees")
        return count

    def _upsert_department(self, feishu_dept: dict[str, Any]) -> None:
        """创建或更新部门。

        Args:
            feishu_dept: 飞书部门数据，包含：
                - department_id: 部门 ID
                - name: 部门名称
                - parent_department_id: 父部门 ID
                - leader_user_id: 部门主管 user_id
        """
        dept_id = str(feishu_dept.get("department_id", ""))
        if not dept_id:
            logger.warning(f"Department missing ID: {feishu_dept}")
            return

        dept = self.db.query(Department).filter_by(id=dept_id).first()
        if not dept:
            dept = Department(id=dept_id)

        dept.name = feishu_dept.get("name", "Unknown")
        dept.parent_id = feishu_dept.get("parent_department_id")
        dept.leader_id = feishu_dept.get("leader_user_id")

        # status: 1=正常，2=停用 → active: true/false
        status = feishu_dept.get("status", 1)
        dept.active = status == 1

        self.db.merge(dept)
        logger.debug(f"Upserted department {dept_id}: {dept.name}")

    def _upsert_employee(self, feishu_emp: dict[str, Any]) -> None:
        """创建或更新员工。

        Args:
            feishu_emp: 飞书员工数据，包含：
                - user_id: 用户 ID
                - name: 姓名
                - open_id: 开放 ID
                - department_ids: 所在部门 ID 列表
                - leader_user_id: 直属主管 user_id
                - email: 邮箱
                - status: {"is_activated": bool, "is_exited": bool}
        """
        emp_id = str(feishu_emp.get("user_id", ""))
        if not emp_id:
            logger.warning(f"Employee missing ID: {feishu_emp}")
            return

        emp = self.db.query(Employee).filter_by(id=emp_id).first()
        if not emp:
            emp = Employee(id=emp_id)

        emp.name = feishu_emp.get("name", "Unknown")
        emp.feishu_open_id = feishu_emp.get("open_id")
        emp.email = feishu_emp.get("email")

        # 主管关系：user_id 而非 open_id
        emp.manager_id = feishu_emp.get("leader_user_id")

        # 部门：取第一个（飞书支持多部门，这里简化处理）
        dept_ids = feishu_emp.get("department_ids", [])
        if dept_ids:
            emp.department_id = str(dept_ids[0])

        # 职位（如果飞书返回）
        emp.position = feishu_emp.get("job_title")

        # 在职状态
        status = feishu_emp.get("status", {})
        if status.get("is_exited"):
            emp.employment_status = "exited"
        elif not status.get("is_activated"):
            emp.employment_status = "inactive"
        else:
            emp.employment_status = "active"

        self.db.merge(emp)
        logger.debug(f"Upserted employee {emp_id}: {emp.name} ({emp.employment_status})")
