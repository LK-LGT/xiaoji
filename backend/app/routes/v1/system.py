"""系统管理 API 路由 - 审计日志、员工管理、部门管理等。"""

from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.db import get_db

router = APIRouter(prefix="/system", tags=["system"])


# ─────────────────────────────────────────────────────
# 仪表盘接口
# ─────────────────────────────────────────────────────

@router.get("/dashboard")
async def get_system_dashboard(db: Session = Depends(get_db)) -> dict:
    """获取系统管理仪表盘数据
    
    包括：
    - 组织、员工、角色统计
    - 配置风险警告
    - 最近审计记录
    
    权限要求: 任何登录用户（暂时开放，后期限制为 system:dashboard:view）
    """
    try:
        # 统计数据（示例实现，后续可从数据库查询）
        department_count = 12  # 组织节点数
        employee_count = 156   # 启用员工数
        role_count = 8         # 启用角色数
        audit_count = 342      # 近90天审计记录
        
        # 配置风险数据
        risks = [
            {
                "code": "employee_no_dept",
                "title": "员工配置不完整",
                "count": 3,
                "level": "high",
                "route": "/system/employees",
            },
            {
                "code": "role_permissions_mismatch",
                "title": "角色权限不同步",
                "count": 1,
                "level": "medium",
                "route": "/system/roles",
            },
            {
                "code": "leader_not_set",
                "title": "部门负责人未设置",
                "count": 2,
                "level": "medium",
                "route": "/system/orgs",
            },
        ]
        
        # 最近审计记录（取前 10 条）
        recent_audits = [
            {
                "id": "audit_001",
                "createdAt": (datetime.utcnow() - timedelta(hours=2)).isoformat() + "Z",
                "username": "admin",
                "operatorName": "管理员",
                "action": "CREATE",
                "actionLabel": "创建员工",
                "targetType": "employee",
                "targetTypeLabel": "员工",
                "targetSummary": "张三",
                "result": "SUCCESS",
            },
            {
                "id": "audit_002",
                "createdAt": (datetime.utcnow() - timedelta(hours=1)).isoformat() + "Z",
                "username": "admin",
                "operatorName": "管理员",
                "action": "UPDATE",
                "actionLabel": "修改员工信息",
                "targetType": "employee",
                "targetTypeLabel": "员工",
                "targetSummary": "李四",
                "result": "SUCCESS",
            },
            {
                "id": "audit_003",
                "createdAt": (datetime.utcnow() - timedelta(minutes=30)).isoformat() + "Z",
                "username": "admin",
                "operatorName": "管理员",
                "action": "LOGIN",
                "actionLabel": "用户登录",
                "targetType": "user",
                "targetTypeLabel": "用户",
                "targetSummary": "admin",
                "result": "SUCCESS",
            },
            {
                "id": "audit_004",
                "createdAt": (datetime.utcnow() - timedelta(hours=3)).isoformat() + "Z",
                "username": "manager",
                "operatorName": "部门主管",
                "action": "DELETE",
                "actionLabel": "删除角色",
                "targetType": "role",
                "targetTypeLabel": "角色",
                "targetSummary": "临时权限",
                "result": "SUCCESS",
            },
            {
                "id": "audit_005",
                "createdAt": (datetime.utcnow() - timedelta(hours=4)).isoformat() + "Z",
                "username": "admin",
                "operatorName": "管理员",
                "action": "EXPORT",
                "actionLabel": "导出员工",
                "targetType": "employee",
                "targetTypeLabel": "员工",
                "targetSummary": "全部员工",
                "result": "SUCCESS",
            },
        ]
        
        return {
            "code": 0,
            "data": {
                "departmentCount": department_count,
                "employeeCount": employee_count,
                "roleCount": role_count,
                "auditCount": audit_count,
                "risks": risks,
                "recentAudits": recent_audits,
            },
        }
    except Exception as e:
        return {
            "code": 500,
            "message": f"获取仪表盘数据失败: {str(e)}",
        }


# ─────────────────────────────────────────────────────
# 审计日志接口
# ─────────────────────────────────────────────────────

@router.get("/options")
async def get_system_options(db: Session = Depends(get_db)) -> dict:
    """获取系统管理选项（员工注册等页面使用）
    
    包括：
    - 部门/小组树形结构
    - 直属负责人列表
    - 可用角色列表
    """
    try:
        # 示例数据（实际应从数据库查询）
        departments = [
            {
                "id": 1,
                "parentId": 0,
                "name": "研发部",
                "code": "RD",
                "orgType": "DEPARTMENT",
                "status": 1,
                "employeeCount": 15,
                "children": [
                    {
                        "id": 11,
                        "parentId": 1,
                        "name": "后端团队",
                        "code": "RD_BACKEND",
                        "orgType": "GROUP",
                        "status": 1,
                        "employeeCount": 8,
                        "children": [],
                    },
                    {
                        "id": 12,
                        "parentId": 1,
                        "name": "前端团队",
                        "code": "RD_FRONTEND",
                        "orgType": "GROUP",
                        "status": 1,
                        "employeeCount": 7,
                        "children": [],
                    },
                ],
            },
            {
                "id": 2,
                "parentId": 0,
                "name": "市场部",
                "code": "MARKET",
                "orgType": "DEPARTMENT",
                "status": 1,
                "employeeCount": 8,
                "children": [],
            },
            {
                "id": 3,
                "parentId": 0,
                "name": "财务部",
                "code": "FINANCE",
                "orgType": "DEPARTMENT",
                "status": 1,
                "employeeCount": 5,
                "children": [],
            },
        ]
        
        leaders = [
            {"id": 1, "label": "张三 - 研发部负责人", "secondary": "研发部", "status": 1},
            {"id": 2, "label": "李四 - 市场部负责人", "secondary": "市场部", "status": 1},
            {"id": 3, "label": "王五 - 财务部负责人", "secondary": "财务部", "status": 1},
        ]
        
        roles = [
            {"id": 1, "name": "管理员", "code": "ADMIN", "status": 1},
            {"id": 2, "name": "部门主管", "code": "MANAGER", "status": 1},
            {"id": 3, "name": "员工", "code": "EMPLOYEE", "status": 1},
            {"id": 4, "name": "总监", "code": "DIRECTOR", "status": 1},
        ]
        
        return {
            "code": 0,
            "data": {
                "departments": departments,
                "leaders": leaders,
                "roles": roles,
            },
        }
    except Exception as e:
        return {
            "code": 500,
            "message": f"获取系统选项失败: {str(e)}",
        }


@router.get("/employees")
async def get_employees(db: Session = Depends(get_db)) -> dict:
    """获取员工列表"""
    return {
        "code": 0,
        "data": {
            "records": [
                {"id": 1, "username": "admin", "realName": "管理员", "mobile": "13800000001", "departmentName": "技术部", "status": 1},
                {"id": 2, "username": "manager", "realName": "部门主管", "mobile": "13800000002", "departmentName": "研发部", "status": 1},
            ],
            "total": 2,
            "pageNo": 1,
            "pageSize": 20,
        },
    }


@router.get("/orgs")
async def get_orgs(db: Session = Depends(get_db)) -> dict:
    """获取组织管理数据"""
    tree_data = [
        {
            "id": 1,
            "name": "研发部",
            "code": "RD",
            "orgType": "DEPARTMENT",
            "leaderName": "张三",
            "leaderUserId": 1,
            "sortNo": 1,
            "status": 1,
            "employeeCount": 12,
            "children": [
                {
                    "id": 11,
                    "name": "后端团队",
                    "code": "RD_BACKEND",
                    "orgType": "GROUP",
                    "leaderName": "李四",
                    "leaderUserId": 2,
                    "sortNo": 1,
                    "status": 1,
                    "employeeCount": 6,
                    "children": [],
                },
                {
                    "id": 12,
                    "name": "前端团队",
                    "code": "RD_FRONTEND",
                    "orgType": "GROUP",
                    "leaderName": "王五",
                    "leaderUserId": 3,
                    "sortNo": 2,
                    "status": 1,
                    "employeeCount": 5,
                    "children": [],
                },
            ],
        },
        {
            "id": 2,
            "name": "产品部",
            "code": "PM",
            "orgType": "DEPARTMENT",
            "leaderName": "赵六",
            "leaderUserId": 4,
            "sortNo": 2,
            "status": 1,
            "employeeCount": 8,
            "children": [],
        },
    ]
    return tree_data


# 别名：/departments 指向 /orgs
@router.get("/departments")
async def get_departments_alias(db: Session = Depends(get_db)):
    """获取部门树（/orgs 的别名）"""
    return await get_orgs(db)


@router.get("/roles")
async def get_roles(db: Session = Depends(get_db)) -> dict:
    """获取角色列表"""
    return {
        "code": 0,
        "data": [
            {"id": 1, "name": "管理员", "code": "ADMIN", "description": "系统管理员", "status": 1},
            {"id": 2, "name": "部门主管", "code": "MANAGER", "description": "部门管理员", "status": 1},
            {"id": 3, "name": "员工", "code": "EMPLOYEE", "description": "普通员工", "status": 1},
        ],
    }


@router.get("/permissions")
async def get_permissions(db: Session = Depends(get_db)) -> dict:
    """获取权限列表"""
    return {
        "code": 0,
        "data": [
            {
                "id": 1,
                "name": "系统管理",
                "code": "system",
                "type": "module",
                "status": 1,
                "children": [
                    {"id": 11, "name": "系统仪表板", "code": "system:dashboard:view", "type": "action", "status": 1, "children": []},
                    {"id": 12, "name": "员工管理", "code": "system:employee:view", "type": "action", "status": 1, "children": []},
                ],
            },
        ],
    }


@router.get("/workday-rules")
async def get_workday_rules(db: Session = Depends(get_db)) -> dict:
    """获取工作日规则"""
    return {
        "code": 0,
        "data": {
            "records": [
                {"id": 1, "ruleDate": "2024-01-01", "ruleType": "HOLIDAY", "description": "元旦", "status": 1},
            ],
            "total": 1,
            "pageNo": 1,
            "pageSize": 20,
        },
    }


@router.get("/week-plan-standard-supplements")
async def get_week_plan_standard_supplements(db: Session = Depends(get_db)) -> dict:
    """获取周计划补录"""
    return {
        "code": 0,
        "data": {
            "records": [],
            "total": 0,
            "pageNo": 1,
            "pageSize": 20,
        },
    }


@router.get("/knowledge")
async def get_knowledge(db: Session = Depends(get_db)) -> dict:
    """获取企业知识库"""
    return {
        "code": 0,
        "data": {
            "records": [],
            "total": 0,
            "pageNo": 1,
            "pageSize": 20,
        },
    }



async def get_audit_options(db: Session = Depends(get_db)) -> dict:
    """获取审计日志筛选选项。"""
    return {
        "code": 0,
        "data": {
            "targetTypes": [
                {"label": "员工", "value": "employee"},
                {"label": "部门", "value": "department"},
                {"label": "考核", "value": "assessment"},
                {"label": "计划", "value": "plan"},
                {"label": "结果", "value": "result"},
            ],
            "actions": [
                {"group": "数据操作", "items": [
                    {"label": "创建", "value": "CREATE"},
                    {"label": "修改", "value": "UPDATE"},
                    {"label": "删除", "value": "DELETE"},
                ]},
                {"group": "流程操作", "items": [
                    {"label": "提交", "value": "SUBMIT"},
                    {"label": "批准", "value": "APPROVE"},
                    {"label": "驳回", "value": "REJECT"},
                ]},
                {"group": "用户操作", "items": [
                    {"label": "登录", "value": "LOGIN"},
                    {"label": "登出", "value": "LOGOUT"},
                    {"label": "导出", "value": "EXPORT"},
                ]},
            ],
            "resultTypes": [
                {"label": "成功", "value": "SUCCESS"},
                {"label": "失败", "value": "FAILED"},
            ],
        },
    }


@router.get("/audits")
async def list_audits(
    page_no: int = Query(1, ge=1, alias="pageNo"),
    page_size: int = Query(20, ge=1, le=100, alias="pageSize"),
    username: Optional[str] = Query(None),
    target_type: Optional[str] = Query(None, alias="targetType"),
    action: Optional[str] = Query(None),
    result: Optional[str] = Query(None),
    start: Optional[str] = Query(None),
    end: Optional[str] = Query(None),
    db: Session = Depends(get_db),
) -> dict:
    """获取审计日志列表。"""
    page = max(1, page_no)
    limit = min(page_size, 100)
    offset = (page - 1) * limit
    
    # 示例数据
    sample_records = [
        {
            "id": "audit_001",
            "createdAt": (datetime.utcnow() - timedelta(hours=2)).isoformat(),
            "username": "admin",
            "operatorName": "管理员",
            "action": "CREATE",
            "actionLabel": "创建员工",
            "targetType": "employee",
            "targetTypeLabel": "员工",
            "targetSummary": "张三",
            "result": "SUCCESS",
            "details": {"name": "张三", "email": "zhangsan@example.com"},
        },
        {
            "id": "audit_002",
            "createdAt": (datetime.utcnow() - timedelta(hours=1)).isoformat(),
            "username": "admin",
            "operatorName": "管理员",
            "action": "UPDATE",
            "actionLabel": "修改员工信息",
            "targetType": "employee",
            "targetTypeLabel": "员工",
            "targetSummary": "李四",
            "result": "SUCCESS",
            "details": {"position": "高级工程师"},
        },
        {
            "id": "audit_003",
            "createdAt": (datetime.utcnow() - timedelta(minutes=30)).isoformat(),
            "username": "admin",
            "operatorName": "管理员",
            "action": "LOGIN",
            "actionLabel": "用户登录",
            "targetType": "user",
            "targetTypeLabel": "用户",
            "targetSummary": "admin",
            "result": "SUCCESS",
            "details": {"ip": "192.168.1.100", "browser": "Chrome"},
        },
    ]
    
    # 应用筛选（简化版）
    records = sample_records
    if result:
        records = [r for r in records if r["result"] == result]
    if target_type:
        records = [r for r in records if r["targetType"] == target_type]
    if action:
        records = [r for r in records if r["action"] == action]
    if username:
        records = [r for r in records if username.lower() in r["username"].lower() or username.lower() in r["operatorName"].lower()]
    
    total = len(records)
    paginated = records[offset : offset + limit]
    
    return {
        "code": 0,
        "data": {
            "records": paginated,
            "total": total,
            "pageNo": page,
            "pageSize": limit,
        },
    }


@router.get("/audits/export")
async def export_audits(
    page_no: int = Query(1, ge=1, alias="pageNo"),
    page_size: int = Query(20, ge=1, le=100, alias="pageSize"),
    username: Optional[str] = Query(None),
    db: Session = Depends(get_db),
) -> dict:
    """导出审计日志。"""
    return {"code": 0, "data": {"message": "正在导出..."}}


# ─────────────────────────────────────────────────────
# 部门管理 CRUD 操作
# ─────────────────────────────────────────────────────

@router.post("/departments")
async def create_department(data: dict, db: Session = Depends(get_db)) -> dict:
    """创建新部门"""
    new_id = 100 + int(__import__('time').time() * 1000) % 1000
    return {
        "id": new_id,
        "name": data.get("name", "新部门"),
        "code": data.get("code", "NEW_DEPT"),
        "orgType": data.get("orgType", "DEPARTMENT"),
        "status": 1,
        "children": [],
    }


@router.put("/departments/{dept_id}")
async def update_department(dept_id: int, data: dict, db: Session = Depends(get_db)) -> dict:
    """更新部门信息"""
    return {
        "id": dept_id,
        "name": data.get("name", "更新的部门"),
        "code": data.get("code", "UPDATED_DEPT"),
        "status": data.get("status", 1),
    }


@router.delete("/departments/{dept_id}")
async def delete_department(dept_id: int, db: Session = Depends(get_db)) -> dict:
    """删除部门"""
    return {"code": 0, "message": "部门已删除"}


# ─────────────────────────────────────────────────────
# 角色管理 CRUD 操作
# ─────────────────────────────────────────────────────

@router.post("/roles")
async def create_role(data: dict, db: Session = Depends(get_db)) -> dict:
    """创建新角色"""
    new_id = 100 + int(__import__('time').time() * 1000) % 1000
    return {
        "id": new_id,
        "name": data.get("name", "新角色"),
        "code": data.get("code", "NEW_ROLE"),
        "description": data.get("description", ""),
        "status": 1,
    }


@router.put("/roles/{role_id}")
async def update_role(role_id: int, data: dict, db: Session = Depends(get_db)) -> dict:
    """更新角色信息"""
    return {
        "id": role_id,
        "name": data.get("name", "更新的角色"),
        "code": data.get("code", "UPDATED_ROLE"),
        "status": data.get("status", 1),
    }


@router.delete("/roles/{role_id}")
async def delete_role(role_id: int, db: Session = Depends(get_db)) -> dict:
    """删除角色"""
    return {"code": 0, "message": "角色已删除"}


# ─────────────────────────────────────────────────────
# 工作日规则 CRUD 操作
# ─────────────────────────────────────────────────────

@router.post("/workday-rules")
async def create_workday_rule(data: dict, db: Session = Depends(get_db)) -> dict:
    """创建工作日规则"""
    new_id = 100 + int(__import__('time').time() * 1000) % 1000
    return {
        "id": new_id,
        "name": data.get("name", "新规则"),
        "description": data.get("description", ""),
        "status": 1,
    }


@router.put("/workday-rules/{rule_id}")
async def update_workday_rule(rule_id: int, data: dict, db: Session = Depends(get_db)) -> dict:
    """更新工作日规则"""
    return {
        "id": rule_id,
        "name": data.get("name", "更新的规则"),
        "status": data.get("status", 1),
    }


@router.delete("/workday-rules/{rule_id}")
async def delete_workday_rule(rule_id: int, db: Session = Depends(get_db)) -> dict:
    """删除工作日规则"""
    return {"code": 0, "message": "规则已删除"}


# ─────────────────────────────────────────────────────
# 周计划补录 CRUD 操作
# ─────────────────────────────────────────────────────

@router.post("/week-plan-standard-supplements")
async def create_week_plan_supplement(data: dict, db: Session = Depends(get_db)) -> dict:
    """创建周计划补录"""
    new_id = 100 + int(__import__('time').time() * 1000) % 1000
    return {
        "id": new_id,
        "name": data.get("name", "新补录"),
        "status": 1,
    }


@router.delete("/week-plan-standard-supplements/{supplement_id}")
async def delete_week_plan_supplement(supplement_id: int, db: Session = Depends(get_db)) -> dict:
    """删除周计划补录"""
    return {"code": 0, "message": "补录已删除"}
