"""【板块1】鉴权权限层 - 使用示例。

本脚本演示如何在 FastAPI 路由中使用 dependencies.py 的函数。

⚠️ 本文件**按功能分节**组织（鉴权 → 路由 → 错误处理中间件 → …），
   每个分节在自己的位置 import 它要用的东西 —— 这是**示例代码的编排方式**，
   不是「import 放错地方」。
   → 整个文件豁免 E402（module-import-not-at-top-of-file）。
"""

# ruff: noqa: E402

from fastapi import Depends, FastAPI, HTTPException
from sqlalchemy.orm import Session

from backend.app.core.db import get_db
from backend.app.dependencies import (
    check_can_submit_review,
    check_can_view_score,
    get_current_hr,
    get_current_manager,
    get_current_user,
    get_direct_reports,
)
from backend.app.domain import models
from backend.app.exceptions import NotAuthenticated, PermissionDenied, RecordNotFound

app = FastAPI()


# ┌─────────────────────────────────────────────────────┐
# │ 示例 1：查看自己或他人的成绩（权限检查）           │
# └─────────────────────────────────────────────────────┘

@app.get("/api/v1/score/{employee_id}")
async def view_employee_score(
    employee_id: str,
    user: models.Employee = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    查询某个员工的成绩。

    权限规则：
    - 自己能查自己
    - 主管能查直属下级
    - HR 能查所有人

    使用 Mock Token 测试：
    - emp_token_001 查看 E001（自己） → 允许
    - emp_token_001 查看 E002（他人） → 拒绝
    - manager_token_001 查看 E001（下级） → 允许
    - hr_token_001 查看任何人 → 允许
    """

    try:
        # 权限检查（自动拒绝越权，无需额外代码）
        check_can_view_score(user, employee_id, db)

        # 业务逻辑：查询 Score
        # score = db.query(models.Score).filter_by(...).first()
        # 这里简化为示例返回

        return {
            "success": True,
            "employee_id": employee_id,
            "viewer": user.name,
            "score": 85.5,
            "rating": "A",
            "message": "数据可见"
        }

    except PermissionDenied as e:
        raise HTTPException(status_code=403, detail=e.message)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ┌─────────────────────────────────────────────────────┐
# │ 示例 2：提交自评或评分（权限 + 状态检查）          │
# └─────────────────────────────────────────────────────┘

@app.post("/api/v1/records/{record_id}/submit")
async def submit_review(
    record_id: str,
    review_data: dict,
    user: models.Employee = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    提交考核记录的自评或评分。

    权限规则：
    - 员工能提交自己的自评
    - 主管能提交直属下级的评分

    使用 Mock Token 测试：
    - emp_token_001 提交自己的 record → 允许
    - emp_token_001 提交他人的 record → 拒绝
    - manager_token_001 提交下级的 record → 允许
    """

    try:
        # 权限检查 + 获取 record 对象
        # record = check_can_submit_review(user, record_id, db)
        check_can_submit_review(user, record_id, db)

        # 业务逻辑：更新 record
        # record.status = "submitted"
        # db.commit()

        return {
            "success": True,
            "record_id": record_id,
            "submitter": user.name,
            "review_data": review_data,
            "message": "提交成功"
        }

    except PermissionDenied as e:
        raise HTTPException(status_code=403, detail=e.message)
    except RecordNotFound as e:
        raise HTTPException(status_code=404, detail=e.message)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ┌─────────────────────────────────────────────────────┐
# │ 示例 3：查看直属下级（主管专用）                    │
# └─────────────────────────────────────────────────────┘

@app.get("/api/v1/subordinates/list")
async def list_subordinates(
    user: models.Employee = Depends(get_current_manager),  # 自动检查是否是主管
    db: Session = Depends(get_db)
):
    """
    查看自己的直属下级列表。

    依赖 get_current_manager 的自动权限检查：
    - 如果没有直属下级，返回 403

    使用 Mock Token 测试：
    - manager_token_001 查看下级 → 允许
    - emp_token_001 查看下级 → 拒绝（无下级）
    - hr_token_001 查看下级 → 允许（HR 也是 manager）
    """


    direct_reports = get_direct_reports(user=user, db=db)

    # 获取下级的详细信息
    subordinates = db.query(models.Employee).filter(
        models.Employee.id.in_(direct_reports)
    ).all()

    return {
        "success": True,
        "manager": user.name,
        "subordinates": [
            {
                "id": emp.id,
                "name": emp.name,
                "position": emp.position,
                "department": emp.department_id
            }
            for emp in subordinates
        ]
    }


# ┌─────────────────────────────────────────────────────┐
# │ 示例 4：HR 专用接口（HR 专用）                      │
# └─────────────────────────────────────────────────────┘

@app.post("/api/v1/admin/approve-rating/{record_id}")
async def approve_rating(
    record_id: str,
    rating: str,
    user: models.Employee = Depends(get_current_hr),  # 自动检查是否是 HR
    db: Session = Depends(get_db)
):
    """
    HR 确认员工最终等级。

    依赖 get_current_hr 的自动权限检查：
    - 如果不是 HR，返回 403

    使用 Mock Token 测试：
    - hr_token_001 确认等级 → 允许
    - manager_token_001 确认等级 → 拒绝
    - emp_token_001 确认等级 → 拒绝
    """

    try:
        record = db.query(models.AssessmentRecord).filter_by(id=record_id).first()
        if not record:
            raise RecordNotFound("AssessmentRecord", record_id)

        # 业务逻辑：确认等级
        # record.confirmed_rating = rating
        # record.confirmed_by = user.id
        # db.commit()

        return {
            "success": True,
            "record_id": record_id,
            "approved_by": user.name,
            "rating": rating,
            "message": "等级已确认"
        }

    except RecordNotFound as e:
        raise HTTPException(status_code=404, detail=e.message)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ┌─────────────────────────────────────────────────────┐
# │ 示例 5：健康检查（无权限要求）                      │
# └─────────────────────────────────────────────────────┘

@app.get("/health")
async def health_check():
    """无权限要求的健康检查端点。"""
    return {
        "status": "ok",
        "message": "板块1 - 鉴权权限层已就绪"
    }


# ┌─────────────────────────────────────────────────────┐
# │ 错误处理中间件（将异常转成 HTTP 响应）             │
# └─────────────────────────────────────────────────────┘

from fastapi.responses import JSONResponse

from backend.app.exceptions import BaseAppError


@app.exception_handler(BaseAppError)
async def app_error_handler(request, exc: BaseAppError):
    """统一处理所有自定义异常。"""
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "code": exc.code,
            "message": exc.message,
            "details": exc.details
        }
    )

@app.exception_handler(NotAuthenticated)
async def not_authenticated_handler(request, exc: NotAuthenticated):
    """处理认证失败。"""
    return JSONResponse(
        status_code=401,
        content={
            "code": "AUTHENTICATION_FAILED",
            "message": exc.message,
            "details": exc.details
        }
    )


# ┌─────────────────────────────────────────────────────┐
# │ 测试脚本：使用 Mock Token 进行集成测试             │
# └─────────────────────────────────────────────────────┘

if __name__ == "__main__":
    """
    本脚本可直接运行进行测试（需要启动 FastAPI 服务）。

    运行方式：
    $ uvicorn BLOCK1_USAGE_EXAMPLE:app --reload

    然后使用 curl 测试各个端点：

    1. 普通员工查看自己的成绩（允许）
       curl -H "Authorization: Bearer emp_token_001" \
            http://localhost:8000/api/v1/score/E001

       预期响应：200 OK
       {
         "success": true,
         "employee_id": "E001",
         "viewer": "刘刚（销售代表）",
         "score": 85.5,
         "rating": "A",
         "message": "数据可见"
       }

    2. 普通员工查看他人的成绩（拒绝）
       curl -H "Authorization: Bearer emp_token_001" \
            http://localhost:8000/api/v1/score/E002

       预期响应：403 Forbidden
       {
         "code": "PERMISSION_DENIED",
         "message": "用户 E001 无权执行 view_score 资源 employee:E002"
       }

    3. 主管查看直属下级的成绩（允许）
       curl -H "Authorization: Bearer manager_token_001" \
            http://localhost:8000/api/v1/score/E001

       预期响应：200 OK

    4. HR 查看任何人的成绩（允许）
       curl -H "Authorization: Bearer hr_token_001" \
            http://localhost:8000/api/v1/score/E001

       预期响应：200 OK

    5. 无效 token（拒绝）
       curl -H "Authorization: Bearer invalid_token" \
            http://localhost:8000/api/v1/score/E001

       预期响应：401 Unauthorized
       {
         "code": "NOT_AUTHENTICATED",
         "message": "Token 无效或已过期"
       }

    6. 缺少 Authorization header（拒绝）
       curl http://localhost:8000/api/v1/score/E001

       预期响应：401 Unauthorized
       {
         "code": "NOT_AUTHENTICATED",
         "message": "缺少 Authorization header"
       }

    7. 主管查看直属下级列表（允许）
       curl -H "Authorization: Bearer manager_token_001" \
            http://localhost:8000/api/v1/subordinates/list

       预期响应：200 OK

    8. 普通员工查看下级列表（拒绝）
       curl -H "Authorization: Bearer emp_token_001" \
            http://localhost:8000/api/v1/subordinates/list

       预期响应：403 Forbidden
       {
         "code": "PERMISSION_DENIED",
         "message": "用户 E001 无权执行 access_manager_interface 资源 主管操作（无下级）"
       }

    9. HR 专用接口（允许）
       curl -X POST -H "Authorization: Bearer hr_token_001" \
            -H "Content-Type: application/json" \
            -d '{"rating": "A"}' \
            http://localhost:8000/api/v1/admin/approve-rating/RECORD_001

       预期响应：200 OK

    10. 主管尝试确认等级（拒绝）
        curl -X POST -H "Authorization: Bearer manager_token_001" \
             -H "Content-Type: application/json" \
             -d '{"rating": "A"}' \
             http://localhost:8000/api/v1/admin/approve-rating/RECORD_001

        预期响应：403 Forbidden
    """

    print(__doc__)
    print("\n🚀 启动 FastAPI 应用：")
    print("   uvicorn BLOCK1_USAGE_EXAMPLE:app --reload")
