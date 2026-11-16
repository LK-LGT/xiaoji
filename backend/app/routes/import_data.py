"""员工批量导入 API 路由。"""

from __future__ import annotations

import csv
import io
import logging

from fastapi import APIRouter, Depends, File, UploadFile
from sqlalchemy.orm import Session

from ..core.db import get_db
from ..dependencies import get_current_user
from ..domain.models import Department, Employee

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/import", tags=["import"], dependencies=[Depends(get_current_user)])


@router.post("/employees-csv")
def import_employees_csv(file: UploadFile = File(...), db: Session = Depends(get_db)):
    """批量导入员工数据（CSV 格式）。

    CSV 格式要求（表头）：
        - id: 员工 ID（唯一）
        - name: 员工名称
        - email: 邮箱
        - department_id: 部门 ID
        - position: 职位
        - manager_id: 直属主管 ID（可选）
        - employment_status: 在职状态（active/inactive/exited，默认 active）

    示例 CSV:
        id,name,email,department_id,position,manager_id,employment_status
        emp001,张三,zhang@company.com,dept001,经理,emp000,active
        emp002,李四,li@company.com,dept001,员工,emp001,active

    Returns:
        导入统计信息
    """
    try:
        # 读取 CSV 文件
        content = file.file.read().decode('utf-8')
        reader = csv.DictReader(io.StringIO(content))

        if not reader.fieldnames:
            return {
                "code": 400,
                "message": "CSV 文件为空或格式错误",
            }

        stats = {
            "total": 0,
            "imported": 0,
            "updated": 0,
            "errors": 0,
            "error_details": [],
        }

        for row_num, row in enumerate(reader, start=2):  # 从第 2 行开始（跳过表头）
            try:
                emp_id = row.get("id", "").strip()
                if not emp_id:
                    stats["errors"] += 1
                    stats["error_details"].append(f"第 {row_num} 行：ID 缺失")
                    continue

                name = row.get("name", "").strip()
                if not name:
                    stats["errors"] += 1
                    stats["error_details"].append(f"第 {row_num} 行：姓名缺失")
                    continue

                # 查询或创建员工
                emp = db.query(Employee).filter_by(id=emp_id).first()
                if emp:
                    stats["updated"] += 1
                else:
                    emp = Employee(id=emp_id)
                    stats["imported"] += 1

                # 更新字段
                emp.name = name
                emp.email = row.get("email", "").strip() or None
                emp.department_id = row.get("department_id", "").strip() or None
                emp.position = row.get("position", "").strip() or None
                emp.manager_id = row.get("manager_id", "").strip() or None
                emp.employment_status = row.get("employment_status", "active").strip() or "active"

                db.merge(emp)
                stats["total"] += 1

            except Exception as e:
                stats["errors"] += 1
                stats["error_details"].append(f"第 {row_num} 行：{str(e)}")
                logger.warning(f"导入员工失败（第 {row_num} 行）: {e}")

        db.commit()

        return {
            "code": 0,
            "message": "员工导入完成",
            "data": stats,
        }

    except Exception as e:
        logger.exception("员工导入失败")
        return {
            "code": 500,
            "message": f"导入失败: {str(e)}",
        }


@router.post("/departments-csv")
def import_departments_csv(file: UploadFile = File(...), db: Session = Depends(get_db)):
    """批量导入部门数据（CSV 格式）。

    CSV 格式要求（表头）：
        - id: 部门 ID（唯一）
        - name: 部门名称
        - parent_id: 父部门 ID（可选）
        - leader_id: 部门主管 ID（可选）
        - active: 是否活跃（true/false，默认 true）

    Returns:
        导入统计信息
    """
    try:
        content = file.file.read().decode('utf-8')
        reader = csv.DictReader(io.StringIO(content))

        if not reader.fieldnames:
            return {
                "code": 400,
                "message": "CSV 文件为空或格式错误",
            }

        stats = {
            "total": 0,
            "imported": 0,
            "updated": 0,
            "errors": 0,
            "error_details": [],
        }

        for row_num, row in enumerate(reader, start=2):
            try:
                dept_id = row.get("id", "").strip()
                if not dept_id:
                    stats["errors"] += 1
                    stats["error_details"].append(f"第 {row_num} 行：ID 缺失")
                    continue

                name = row.get("name", "").strip()
                if not name:
                    stats["errors"] += 1
                    stats["error_details"].append(f"第 {row_num} 行：部门名称缺失")
                    continue

                # 查询或创建部门
                dept = db.query(Department).filter_by(id=dept_id).first()
                if dept:
                    stats["updated"] += 1
                else:
                    dept = Department(id=dept_id)
                    stats["imported"] += 1

                # 更新字段
                dept.name = name
                dept.parent_id = row.get("parent_id", "").strip() or None
                dept.leader_id = row.get("leader_id", "").strip() or None

                active_str = row.get("active", "true").strip().lower()
                dept.active = active_str in ("true", "1", "yes")

                db.merge(dept)
                stats["total"] += 1

            except Exception as e:
                stats["errors"] += 1
                stats["error_details"].append(f"第 {row_num} 行：{str(e)}")
                logger.warning(f"导入部门失败（第 {row_num} 行）: {e}")

        db.commit()

        return {
            "code": 0,
            "message": "部门导入完成",
            "data": stats,
        }

    except Exception as e:
        logger.exception("部门导入失败")
        return {
            "code": 500,
            "message": f"导入失败: {str(e)}",
        }
