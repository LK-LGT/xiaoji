"""组织结构查询 API 路由。

包括：
- 获取完整组织树
- 按部门查询员工
- 获取部门详情
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from ..core.db import get_db
from ..dependencies import get_current_user
from ..domain.models import Department, Employee

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/organizations", tags=["organizations"], dependencies=[Depends(get_current_user)])


# -------------------------------------------------------
# 组织树相关接口
# -------------------------------------------------------


@router.get("/departments/tree")
def get_departments_tree(db: Session = Depends(get_db)) -> dict[str, Any]:
    """获取完整的组织部门树。

    Returns:
        {
            "code": 0,
            "data": {
                "tree": [
                    {
                        "id": "...",
                        "name": "...",
                        "employees_count": 10,
                        "direct_count": 3,
                        "children": [...]
                    }
                ],
                "total_departments": 30,
                "total_employees": 92
            }
        }
    """
    # 只查飞书真实数据（不含测试部门）
    depts = (
        db.query(Department)
        .filter(~Department.id.like("DEPT_%"))
        .all()
    )

    dept_map = {d.id: d for d in depts}

    # 计算每个部门的直属员工数
    def get_direct_employee_count(dept_id: str) -> int:
        return db.query(Employee).filter(Employee.department_id == dept_id).count()

    # 递归计算部门及子部门总人数
    def count_total_employees(dept_id: str, visited: set | None = None) -> int:
        if visited is None:
            visited = set()
        if dept_id in visited:
            return 0
        visited.add(dept_id)

        # 直属员工
        direct = get_direct_employee_count(dept_id)

        # 子部门的人
        children = [d for d in depts if d.parent_id == dept_id]
        total = direct
        for child in children:
            total += count_total_employees(child.id, visited)

        return total

    # 构建树结构
    def build_tree_node(dept_id: str) -> dict[str, Any]:
        dept = dept_map.get(dept_id)
        if not dept:
            return {}

        direct_count = get_direct_employee_count(dept_id)
        total_count = count_total_employees(dept_id)

        children_depts = [d for d in depts if d.parent_id == dept_id]
        children_depts.sort(key=lambda x: x.name or "")

        return {
            "id": dept.id,
            "name": dept.name or "(根部门)",
            "leader_id": dept.leader_id,
            "direct_count": direct_count,
            "total_count": total_count,
            "children": [build_tree_node(child.id) for child in children_depts],
        }

    # 找一级部门
    root_depts = [
        d for d in depts
        if d.parent_id == "0" or d.parent_id == 0 or not d.parent_id
    ]
    root_depts.sort(key=lambda x: x.name or "")

    tree = [build_tree_node(d.id) for d in root_depts]

    total_emps = db.query(Employee).filter(
        ~Employee.department_id.like("DEPT_%")
    ).count()

    return {
        "code": 0,
        "data": {
            "tree": tree,
            "total_departments": len(depts),
            "total_employees": total_emps,
        },
    }


@router.get("/org-chart")
def get_org_chart(db: Session = Depends(get_db)) -> dict[str, Any]:
    """获取轻量级组织结构（仅用于绘制图表）。

    Returns:
        {
            "code": 0,
            "data": {
                "nodes": [
                    {"id": "...", "name": "...", "parent": "..."}
                ],
                "stats": {
                    "total_departments": 30,
                    "total_employees": 92
                }
            }
        }
    """
    depts = (
        db.query(Department)
        .filter(~Department.id.like("DEPT_%"))
        .all()
    )

    nodes = []
    for d in depts:
        nodes.append({
            "id": d.id,
            "name": d.name or "(根部门)",
            "parent": d.parent_id or "root",
            "type": "department",
        })

    return {
        "code": 0,
        "data": {
            "nodes": nodes,
            "stats": {
                "total_departments": len(depts),
                "total_employees": db.query(Employee).filter(
                    ~Employee.department_id.like("DEPT_%")
                ).count(),
            },
        },
    }


# -------------------------------------------------------
# 部门相关接口
# -------------------------------------------------------


@router.get("/departments/{department_id}")
def get_department(
    department_id: str,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """获取部门详情及所有员工。

    Args:
        department_id: 部门 ID

    Returns:
        {
            "code": 0,
            "data": {
                "department": {...},
                "employees": [...],
                "total": 10
            }
        }
    """
    dept = db.query(Department).filter(Department.id == department_id).first()
    if not dept:
        raise HTTPException(status_code=404, detail="Department not found")

    # 查该部门的直属员工
    employees = (
        db.query(Employee)
        .filter(Employee.department_id == department_id)
        .all()
    )

    return {
        "code": 0,
        "data": {
            "department": {
                "id": dept.id,
                "name": dept.name,
                "parent_id": dept.parent_id,
                "leader_id": dept.leader_id,
            },
            "employees": [
                {
                    "id": e.id,
                    "name": e.name,
                    "email": e.email,
                    "position": e.position,
                    "department_id": e.department_id,
                }
                for e in employees
            ],
            "total": len(employees),
        },
    }


@router.get("/departments")
def list_departments(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=100),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """分页获取所有部门。

    Returns:
        {
            "code": 0,
            "data": {
                "items": [...],
                "total": 30,
                "page": 1,
                "page_size": 50
            }
        }
    """
    query = db.query(Department).filter(~Department.id.like("DEPT_%"))
    total = query.count()

    items = (
        query.order_by(Department.name)
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )

    return {
        "code": 0,
        "data": {
            "items": [
                {
                    "id": d.id,
                    "name": d.name,
                    "parent_id": d.parent_id,
                    "leader_id": d.leader_id,
                }
                for d in items
            ],
            "total": total,
            "page": page,
            "page_size": page_size,
        },
    }


# -------------------------------------------------------
# 员工相关接口
# -------------------------------------------------------


@router.get("/employees/{employee_id}")
def get_employee(
    employee_id: str,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """获取员工详情。

    Args:
        employee_id: 员工 ID

    Returns:
        {
            "code": 0,
            "data": {
                "id": "...",
                "name": "...",
                "email": "...",
                "department": {...}
            }
        }
    """
    emp = db.query(Employee).filter(Employee.id == employee_id).first()
    if not emp:
        raise HTTPException(status_code=404, detail="Employee not found")

    dept = (
        db.query(Department)
        .filter(Department.id == emp.department_id)
        .first()
    )

    return {
        "code": 0,
        "data": {
            "id": emp.id,
            "name": emp.name,
            "email": emp.email,
            "position": emp.position,
            "department": {
                "id": dept.id if dept else None,
                "name": dept.name if dept else None,
            } if emp.department_id else None,
        },
    }


@router.get("/employees")
def list_employees(
    department_id: str | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=100),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """分页获取员工列表。

    Args:
        department_id: 过滤部门 ID（可选）
        page: 页码
        page_size: 每页数量

    Returns:
        {
            "code": 0,
            "data": {
                "items": [...],
                "total": 92,
                "page": 1,
                "page_size": 50
            }
        }
    """
    query = db.query(Employee).filter(~Employee.department_id.like("DEPT_%"))

    if department_id:
        query = query.filter(Employee.department_id == department_id)

    total = query.count()

    items = (
        query.order_by(Employee.name)
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )

    return {
        "code": 0,
        "data": {
            "items": [
                {
                    "id": e.id,
                    "name": e.name,
                    "email": e.email,
                    "position": e.position,
                    "department_id": e.department_id,
                }
                for e in items
            ],
            "total": total,
            "page": page,
            "page_size": page_size,
        },
    }
