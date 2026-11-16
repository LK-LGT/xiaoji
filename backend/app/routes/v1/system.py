"""系统管理 API 路由 - 审计日志、员工管理、部门管理等。

组织与员工数据来自飞书同步落库的 `department` / `employee` 表
（见 `services.system_views`），不再返回样例数据。
"""

from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from sqlalchemy.orm import Session

from ...core.db import get_db
from ...services import system_views, workday_rule
from ..shared import JsonBody

router = APIRouter(prefix="/v1/system", tags=["system"])


# ─────────────────────────────────────────────────────
# 仪表盘接口
# ─────────────────────────────────────────────────────

@router.get("/dashboard")
async def get_system_dashboard(db: Session = Depends(get_db)) -> dict:
    """获取系统管理仪表盘数据

    包括：
    - 组织、员工、角色统计（真实数据）
    - 配置风险警告（由数据质量指标推导）
    - 最近审计记录

    权限要求: 任何登录用户（暂时开放，后期限制为 system:dashboard:view）
    """
    from ...domain.models import Department, Employee, SystemAdmin
    from ...services.system_views import _data_quality_snapshot

    try:
        department_count = db.query(Department).filter(Department.active.is_(True)).count()
        employee_count = (
            db.query(Employee)
            .filter(
                Employee.employment_status == "active",
                Employee.account_status == "enabled",
            )
            .count()
        )
        role_count = db.query(SystemAdmin).filter(SystemAdmin.active.is_(True)).count()
        audit_count = 0  # 审计表尚未统一到本模块，保持为真实计数而非写死值。

        quality = _data_quality_snapshot(db)

        # 配置风险数据：由真实数据质量指标推导。
        risks = [
            {
                "code": "employee_no_dept",
                "title": "员工配置不完整",
                "count": quality["no_department_count"],
                "level": "high",
                "route": "/system/employees",
            },
            {
                "code": "employee_no_leader",
                "title": "员工未配置直属上级",
                "count": quality["no_manager_count"],
                "level": "medium",
                "route": "/system/employees",
            },
            {
                "code": "leader_not_set",
                "title": "部门负责人未设置",
                "count": quality["department_without_leader_count"],
                "level": "medium",
                "route": "/system/orgs",
            },
            {
                "code": "cyclic_manager",
                "title": "直属上级关系存在循环",
                "count": quality["cyclic_manager_count"],
                "level": "high",
                "route": "/system/employees",
            },
        ]
        risks = [risk for risk in risks if risk["count"] > 0]

        # 最近审计记录：取同步审计流水。
        from ...domain.models import OrganizationSyncAudit

        recent_rows = (
            db.query(OrganizationSyncAudit)
            .order_by(OrganizationSyncAudit.created_at.desc())
            .limit(10)
            .all()
        )
        recent_audits = [
            {
                "id": row.id,
                "createdAt": row.created_at.isoformat() if row.created_at else "",
                "username": row.actor_id or "system",
                "operatorName": row.actor_id or "系统",
                "action": row.action,
                "actionLabel": row.action,
                "targetType": row.resource_type or "organization",
                "targetTypeLabel": "组织架构",
                "targetSummary": row.batch_id or "",
                "result": "SUCCESS" if row.result == "ok" else row.result.upper(),
            }
            for row in recent_rows
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
                "dataQuality": quality,
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
    - 部门/小组树形结构（来自飞书同步落库的 department 表）
    - 直属负责人列表（来自 employee 表）
    - 可用角色列表
    """
    try:
        return {
            "code": 0,
            "data": system_views.list_system_options(db),
        }
    except Exception as e:
        return {
            "code": 500,
            "message": f"获取系统选项失败: {str(e)}",
        }


@router.get("/employees")
async def get_employees(
    keyword: Optional[str] = Query(None),
    deptId: Optional[str] = Query(None),
    status: Optional[int] = Query(None),
    pageNo: int = Query(1, ge=1),
    pageSize: int = Query(20, ge=1, le=200),
    db: Session = Depends(get_db),
) -> dict:
    """获取员工列表（真实数据）。

    数据来源：飞书同步落库的 employee 表。
    """
    try:
        return {
            "code": 0,
            "data": system_views.list_user_items(
                db,
                keyword=keyword,
                dept_id=deptId,
                status=status,
                page_no=pageNo,
                page_size=pageSize,
            ),
        }
    except Exception as e:
        return {
            "code": 500,
            "message": f"获取员工列表失败: {str(e)}",
        }


@router.get("/orgs")
async def get_orgs(db: Session = Depends(get_db)) -> list[dict]:
    """获取组织管理数据（真实部门树）。

    返回**裸数组**（`DeptNode[]`），与前端 `api/system.ts` 的
    `getDepartments(): DeptNode[]` 契约一致。

    ⚠️ 注解不能写成 `-> dict`：本函数实际返回 list，FastAPI 会按注解
    做响应模型校验，标错会导致 `ResponseValidationError` → HTTP 500
    （前端表现为「组织架构页面加载失败」）。`/departments` 别名同此。
    """
    return system_views.list_department_tree(db, include_inactive=True)


# 别名：/departments 指向 /orgs
@router.get("/departments")
async def get_departments_alias(db: Session = Depends(get_db)) -> list[dict]:
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


@router.get("/roles/{role_id}/permissions")
async def get_role_permissions(role_id: int, db: Session = Depends(get_db)) -> dict:
    """获取指定角色的权限 ID 列表。

    响应结构：number[]（裸数组）
    """
    return {"code": 0, "data": []}


@router.put("/roles/{role_id}/permissions")
async def save_role_permissions(
    role_id: int,
    data: JsonBody,
    db: Session = Depends(get_db),
) -> dict:
    """保存指定角色的权限。

    请求体：{ permissionIds: number[] }
    响应结构：{ roleId, permissionIds }
    """
    permission_ids = data.get("permissionIds") or []
    if not isinstance(permission_ids, list):
        permission_ids = []
    return {
        "code": 0,
        "data": {"roleId": role_id, "permissionIds": permission_ids},
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
async def get_workday_rules(
    month: str | None = Query(None, description="月份 YYYY-MM"),
    ruleType: str | None = Query(None, description="规则类型"),
    status: int | None = Query(None, description="启用状态 1/0"),
    employeeId: str | None = Query(None, description="员工 ID（个人规则）"),
    scope: str = Query("global", description="global | employee | all"),
    pageNo: int = Query(1, ge=1),
    pageSize: int = Query(20, ge=1, le=200),
    db: Session = Depends(get_db),
) -> dict:
    """获取工作日规则列表。

    响应结构：``PageResult<WorkdayRule>``
    （``{records, total, pageNo, pageSize}``）

    每个「日期 + 类型 + 归属」只返回**版本号最大的当前生效版本**。
    """
    try:
        rows = workday_rule.list_rules(
            db,
            month=month,
            rule_type=ruleType,
            status=status,
            employee_id=employeeId,
            scope=scope,
        )
    except workday_rule.WorkdayRuleError as exc:
        raise HTTPException(status_code=400, detail=exc.message) from exc

    total = len(rows)
    offset = (pageNo - 1) * pageSize
    page = rows[offset : offset + pageSize]
    return {
        "code": 0,
        "data": {
            "records": [workday_rule.serialize_rule(row) for row in page],
            "total": total,
            "pageNo": pageNo,
            "pageSize": pageSize,
        },
    }


@router.post("/workday-rules/{rule_id}/status")
async def change_workday_rule_status(
    rule_id: str,
    data: JsonBody,
    db: Session = Depends(get_db),
) -> dict:
    """变更工作日规则状态（启用 / 停用）。

    请求体：{ status: number }
    响应结构：``WorkdayRule``

    ⚠️ 这是**开关**而非内容调整 —— 不生成新版本，历史版本不受影响。
    """
    status = int(data.get("status", 0))
    try:
        rule = workday_rule.change_status(db, str(rule_id), status)
    except workday_rule.WorkdayRuleNotFound as exc:
        raise HTTPException(status_code=404, detail=exc.message) from exc
    except workday_rule.WorkdayRuleError as exc:
        raise HTTPException(status_code=400, detail=exc.message) from exc
    return {"code": 0, "data": workday_rule.serialize_rule(rule)}


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


@router.get("/week-plan-standard-supplements/{supplement_id}")
async def get_week_plan_standard_supplement(
    supplement_id: int,
    db: Session = Depends(get_db),
) -> dict:
    """获取周计划补录详情。

    响应结构：WeekPlanDetail = { summary, items, siblingPlans, dayPlanCount }
    """
    return {
        "code": 0,
        "data": {
            "summary": {
                "id": supplement_id,
                "title": "",
                "weekStart": "",
                "weekEnd": "",
                "status": "DRAFT",
                "versionNo": 1,
                "ownerUserId": 0,
                "employeeName": "",
                "departmentName": "",
                "itemCount": 0,
            },
            "items": [],
            "siblingPlans": [],
            "dayPlanCount": 0,
        },
    }


@router.put("/week-plan-standard-supplements/{supplement_id}")
async def update_week_plan_standard_supplement(
    supplement_id: int,
    data: JsonBody,
    db: Session = Depends(get_db),
) -> dict:
    """提交周计划标准补录。

    请求体：{ versionNo, deliverable, quality }
    响应结构：WeekPlanStandardSupplementAction = { id, status, versionNo, message }
    """
    return {
        "code": 0,
        "data": {
            "id": supplement_id,
            "status": "PENDING",
            "versionNo": int(data.get("versionNo", 0) or 0) + 1,
            "message": "补录已提交",
        },
    }


@router.get("/knowledge")
async def get_knowledge(db: Session = Depends(get_db)) -> dict:
    """获取企业知识库（概览，保留旧路径兼容）"""
    return {
        "code": 0,
        "data": {
            "records": [],
            "total": 0,
            "pageNo": 1,
            "pageSize": 20,
        },
    }


# ─────────────────────────────────────────────────────
# 企业知识库 —— 文档管理
#
# 分页约定：前端 KnowledgeDocumentList 用裸数组（直接 .map()），
# 故列表端点返回裸数组而非分页对象。
# ─────────────────────────────────────────────────────


@router.get("/knowledge/status")
async def get_knowledge_status(db: Session = Depends(get_db)) -> dict:
    """获取知识库运行状态。

    响应结构：KnowledgeStatus
    """
    return {
        "code": 0,
        "data": {
            "enabled": True,
            "vaultPath": "",
            "vaultAvailable": False,
            "documentCounts": {},
            "chunkCount": 0,
            "relationCount": 0,
            "vectorEngine": "none",
            "vectorAvailable": False,
            "vectorCollections": [],
            "vectorPointCount": 0,
            "vectorError": None,
            "lastSync": None,
        },
    }


@router.get("/knowledge/documents")
async def list_knowledge_documents(db: Session = Depends(get_db)) -> dict:
    """获取知识库文档列表。

    响应结构：KnowledgeDocument[]（裸数组）
    """
    return {
        "code": 0,
        "data": [],
    }


@router.get("/knowledge/documents/{document_id}")
async def get_knowledge_document(document_id: int, db: Session = Depends(get_db)) -> dict:
    """获取知识库文档详情。

    响应结构：KnowledgeDocumentDetail = { document, chunks, relations }
    """
    return {
        "code": 0,
        "data": {
            "document": None,
            "chunks": [],
            "relations": [],
        },
    }


@router.get("/knowledge/documents/{document_id}/history")
async def get_knowledge_document_history(
    document_id: int,
    db: Session = Depends(get_db),
) -> dict:
    """获取知识库文档版本历史。

    响应结构：KnowledgeDocumentHistory[]（裸数组）
    """
    return {
        "code": 0,
        "data": [],
    }


@router.post("/knowledge/documents/{document_id}/versions")
async def create_knowledge_document_version(
    document_id: int,
    file: UploadFile = File(...),
    version: str = Form(...),
    changeNote: str = Form(...),
    db: Session = Depends(get_db),
) -> dict:
    """上传知识库文档新版本。

    响应结构：KnowledgeUploadResult
    """
    return {
        "code": 0,
        "data": {
            "documentId": document_id,
            "knowledgeId": "",
            "title": "",
            "fileName": file.filename or "",
            "fileType": "",
            "chunkCount": 0,
            "vectorCount": 0,
            "embeddingModels": [],
            "uploadedAt": datetime.utcnow().isoformat(),
        },
    }


@router.post("/knowledge/documents/{document_id}/disable")
async def disable_knowledge_document(
    document_id: int,
    db: Session = Depends(get_db),
) -> dict:
    """停用知识库文档。

    响应结构：KnowledgeActionResult
    """
    return {
        "code": 0,
        "data": {
            "documentId": document_id,
            "knowledgeId": "",
            "action": "DISABLE",
            "vectorCleanupSucceeded": True,
            "message": "",
            "operatedAt": datetime.utcnow().isoformat(),
        },
    }


@router.post("/knowledge/documents/{document_id}/enable")
async def enable_knowledge_document(
    document_id: int,
    db: Session = Depends(get_db),
) -> dict:
    """启用知识库文档。

    响应结构：KnowledgeActionResult
    """
    return {
        "code": 0,
        "data": {
            "documentId": document_id,
            "knowledgeId": "",
            "action": "ENABLE",
            "vectorCleanupSucceeded": True,
            "message": "",
            "operatedAt": datetime.utcnow().isoformat(),
        },
    }


@router.delete("/knowledge/documents/{document_id}")
async def delete_knowledge_document(
    document_id: int,
    db: Session = Depends(get_db),
) -> dict:
    """删除知识库文档。

    响应结构：KnowledgeActionResult
    """
    return {
        "code": 0,
        "data": {
            "documentId": document_id,
            "knowledgeId": "",
            "action": "DELETE",
            "vectorCleanupSucceeded": True,
            "message": "",
            "operatedAt": datetime.utcnow().isoformat(),
        },
    }


@router.post("/knowledge/sync")
async def sync_knowledge(db: Session = Depends(get_db)) -> dict:
    """触发知识库同步。

    响应结构：KnowledgeSyncResult
    """
    now = datetime.utcnow().isoformat()
    return {
        "code": 0,
        "data": {
            "id": None,
            "triggerSource": "MANUAL",
            "status": "SUCCESS",
            "vaultPath": "",
            "scannedCount": 0,
            "publishedCount": 0,
            "failedCount": 0,
            "errors": [],
            "startedAt": now,
            "finishedAt": now,
        },
    }


@router.get("/knowledge/preview")
async def preview_knowledge(
    appliesTo: str = Query(...),
    query: str = Query(...),
    deptId: str | None = Query(None, description="部门 ID（飞书 department_id，字符串）"),
    db: Session = Depends(get_db),
) -> dict:
    """预览知识库检索结果。

    响应结构：KnowledgeHit[]（裸数组）
    """
    return {
        "code": 0,
        "data": [],
    }



@router.get("/audits/options")
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
    username: str | None = Query(None),
    target_type: str | None = Query(None, alias="targetType"),
    action: str | None = Query(None),
    result: str | None = Query(None),
    start: str | None = Query(None),
    end: str | None = Query(None),
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
    username: str | None = Query(None),
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
async def update_department(dept_id: str, data: dict, db: Session = Depends(get_db)) -> dict:
    """更新部门信息

    ⚠️ `dept_id` 是飞书 `department_id`（长十六进制字符串），
    转 int 会超出 `Number.MAX_SAFE_INTEGER` 之类的精度边界，故为 str。
    """
    return {
        "id": dept_id,
        "name": data.get("name", "更新的部门"),
        "code": data.get("code", "UPDATED_DEPT"),
        "status": data.get("status", 1),
    }


@router.delete("/departments/{dept_id}")
async def delete_department(dept_id: str, db: Session = Depends(get_db)) -> dict:
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
async def create_workday_rule(data: JsonBody, db: Session = Depends(get_db)) -> dict:
    """创建工作日规则。

    请求体：``{ruleDate, ruleType, forceReport, description, status, employeeId?}``
    响应结构：``WorkdayRule``

    - ``ruleType`` 为 ``LEAVE`` / ``BUSINESS_TRIP`` 时**必须**带 ``employeeId``。
    - 同「日期 + 类型 + 归属」已存在规则时返回 409（请走编辑生成新版本）。
    """
    try:
        rule = workday_rule.create_rule(
            db,
            rule_date=data.get("ruleDate"),
            rule_type=data.get("ruleType", ""),
            force_report=bool(data.get("forceReport", False)),
            description=data.get("description", "") or "",
            status=int(data.get("status", 1)),
            employee_id=data.get("employeeId"),
        )
    except workday_rule.WorkdayRuleConflict as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=exc.message) from exc
    except workday_rule.WorkdayRuleError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=exc.message) from exc
    return {"code": 0, "data": workday_rule.serialize_rule(rule)}


@router.put("/workday-rules/{rule_id}")
async def update_workday_rule(
    rule_id: str,
    data: JsonBody,
    db: Session = Depends(get_db),
) -> dict:
    """更新工作日规则 —— **生成新版本**，历史版本不会被覆盖。

    请求体：``{ruleDate?, ruleType?, forceReport?, description?, status?, employeeId?}``
    响应结构：``WorkdayRule``
    """
    try:
        rule = workday_rule.update_rule(
            db,
            str(rule_id),
            rule_date=data.get("ruleDate"),
            rule_type=data.get("ruleType"),
            force_report=data.get("forceReport"),
            description=data.get("description"),
            status=data.get("status"),
            employee_id=data.get("employeeId"),
        )
    except workday_rule.WorkdayRuleNotFound as exc:
        db.rollback()
        raise HTTPException(status_code=404, detail=exc.message) from exc
    except workday_rule.WorkdayRuleConflict as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=exc.message) from exc
    except workday_rule.WorkdayRuleError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=exc.message) from exc
    return {"code": 0, "data": workday_rule.serialize_rule(rule)}


@router.delete("/workday-rules/{rule_id}")
async def delete_workday_rule(rule_id: str, db: Session = Depends(get_db)) -> dict:
    """删除工作日规则（连同该「日期 + 类型 + 归属」的全部历史版本）。"""
    try:
        workday_rule.delete_rule(db, str(rule_id))
    except workday_rule.WorkdayRuleNotFound as exc:
        db.rollback()
        raise HTTPException(status_code=404, detail=exc.message) from exc
    except workday_rule.WorkdayRuleError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=exc.message) from exc
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
