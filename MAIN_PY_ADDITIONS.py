"""补充的 API 路由（P0-2 改动 + 缺失的路由）。

这些路由应该添加到 main.py 的末尾。
"""

# ┌─────────────────────────────────────────────────────┐
# │ P0-2：Q&A 查询 API（改成 POST）                     │
# │ P0-1：progress / result 拆分                        │
# │ P0-4：数据质量 + 隐私脱敏                           │
# │ P2-1：k-匿名 检查                                  │
# └─────────────────────────────────────────────────────┘


@app.post("/v1/api/qa/query", response_model=dict, tags=["QA"])
def query_assessment_data(
    request: schemas.QAQueryRequest,
    current_user: models.Employee = Depends(dependencies.get_current_user),
    db: Session = Depends(get_db)
) -> dict[str, Any]:
    """
    Q&A 查询接口（改成 POST，参考 P0-2）。
    
    查询类型（参考 P0-1）：
    - self_progress：我的月中进度（未确认的数据）
    - self_result：我的月末成绩（已确认）
    - colleague_result：某员工的成绩（权限检查）
    - peer_performance_distribution：部门等级分布（聚合，不排名，参考 P2-2）
    
    关键原则（P0/P2）：
    - 权限来自 token（current_user），不信 URL 参数
    - 响应含 data_quality（替代 confidence）
    - 不暴露 score 字段
    - < 5 人时隐藏平均分（k-匿名，P2-1）
    """
    
    try:
        # 1. 权限检查 + 数据查询
        if request.query_type == "self_progress":
            if request.target_employee_id != current_user.id:
                raise exceptions.PermissionDenied(
                    current_user.id,
                    f"query {request.query_type}",
                    f"employee:{request.target_employee_id}"
                )
            progress_data = qa_service.query_self_progress(
                request.target_employee_id,
                request.task_id,
                db
            )
            data = progress_data.model_dump()
        
        elif request.query_type == "self_result":
            if request.target_employee_id != current_user.id:
                raise exceptions.PermissionDenied(
                    current_user.id,
                    "query self_result",
                    f"employee:{request.target_employee_id}"
                )
            result_data = qa_service.query_self_result(
                request.target_employee_id,
                request.task_id,
                db
            )
            data = result_data.model_dump()
        
        elif request.query_type == "colleague_result":
            # 主管查下级，或 HR 查任何人
            dependencies.check_can_view_score(
                current_user,
                request.target_employee_id,
                db
            )
            result_data = qa_service.query_colleague_result(
                request.target_employee_id,
                request.task_id,
                db
            )
            data = result_data.model_dump()
        
        elif request.query_type == "peer_performance_distribution":
            # 部门汇总（权限检查：只能查自己部门）
            target_emp = db.get(models.Employee, request.target_employee_id)
            if not target_emp:
                raise exceptions.RecordNotFound("Employee", request.target_employee_id)
            
            # TODO: 实现部门权限检查
            # 通常：HR 无限制，主管只能查直属下级的部门
            
            summary_data = qa_service.query_department_summary(
                target_emp.department_id,
                request.task_id,
                db
            )
            data = summary_data.model_dump()
        
        else:
            raise exceptions.DataValidationError(
                f"未知查询类型: {request.query_type}",
                field="query_type"
            )
        
        # 2. 计算数据质量（参考 P0-5）
        data_quality = schemas.DataQualityInfo(
            completeness=0.95,  # TODO: 从 TaskSnapshot 计算
            as_of=datetime.now().isoformat(),
            degraded=False,
            citations=["AssessmentRecord", "Score", "MetricValue"]
        )
        
        return {
            "query_type": request.query_type,
            "data": data,
            "data_quality": data_quality.model_dump()
        }
    
    except exceptions.BaseAppError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    except NotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        # 未预期的异常
        raise HTTPException(status_code=500, detail="内部服务器错误") from exc


# ┌─────────────────────────────────────────────────────┐
# │ 主管评分 API（补全的路由，P0-3）                   │
# └─────────────────────────────────────────────────────┘


class ManagerReviewReq(BaseModel):
    """主管提交评分。"""
    manager_comment: str = Field(..., min_length=1, max_length=2000)
    confirmed_rating: str = Field(..., pattern="^[A-D]$")
    confidence: Optional[float] = Field(None, ge=0.0, le=1.0)


@app.post("/v1/assessment/records/{record_id}/manager-review", tags=["Review"])
def submit_manager_review(
    record_id: str,
    request: ManagerReviewReq,
    current_user: models.Employee = Depends(dependencies.get_current_user),
    db: Session = Depends(get_db)
) -> dict[str, Any]:
    """
    主管提交评分（P0-3 权限模型）。
    
    流程：
    1. 权限检查：只能对直属下级评分
    2. 验证数据：评分等级必须是 A/B/C/D
    3. 触发计分：调用 scoring.compute_score()
    4. 保存结果：更新 Record + Score
    5. 返回：Score + RatingDraft
    
    关键（P0-3）：
    - 权限来自 token
    - 分数由规则引擎计算，不信任主管的 score
    - 主管评分作为输入进入算式
    """
    
    try:
        # 1. 权限检查
        record = db.get(models.AssessmentRecord, record_id)
        if not record:
            raise exceptions.RecordNotFound("AssessmentRecord", record_id)
        
        target_emp = db.get(models.Employee, record.employee_id)
        if not target_emp or target_emp.manager_id != current_user.id:
            raise exceptions.PermissionDenied(
                current_user.id,
                "submit_manager_review",
                f"record:{record_id}"
            )
        
        # 2. 验证等级
        if request.confirmed_rating not in ("A", "B", "C", "D"):
            raise exceptions.DataValidationError(
                f"等级必须是 A/B/C/D，得到 {request.confirmed_rating}",
                field="confirmed_rating"
            )
        
        # 3. 更新 Record
        record.manager_comment = request.manager_comment
        record.confirmed_rating = request.confirmed_rating
        record.stage = "confirmation"
        db.add(record)
        db.flush()
        
        # 4. 触发自动计分
        result = review_service.compute_score(db, record_id=record_id)
        review_service.persist_score(db, record_id=record_id, result=result)
        
        db.commit()
        
        return {
            "status": "success",
            "record_id": record_id,
            "score": result.to_dict() if hasattr(result, "to_dict") else result,
            "confirmed_rating": request.confirmed_rating,
            "stage": "confirmation"
        }
    
    except exceptions.BaseAppError as exc:
        db.rollback()
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    except InvalidState as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=500, detail="内部服务器错误") from exc


# ┌─────────────────────────────────────────────────────┐
# │ 添加指标值路由（补全 P0-5 data_quality）           │
# └─────────────────────────────────────────────────────┘


@app.post("/v1/assessment/records/{record_id}/metrics", tags=["Metrics"])
def add_metric_to_record(
    record_id: str,
    request: schemas.MetricValueRequest,
    current_user: models.Employee = Depends(dependencies.get_current_user),
    db: Session = Depends(get_db)
) -> dict[str, Any]:
    """
    添加指标值到某条 Record（P0-5 data_quality 相关）。
    
    关键决策②：考勤异常必须被拒。
    """
    
    try:
        record = db.get(models.AssessmentRecord, record_id)
        if not record:
            raise exceptions.RecordNotFound("AssessmentRecord", record_id)
        
        # 权限检查：只能 HR 或该记录的主管
        dependencies.check_can_add_metric(record_id, db)
        
        # 添加指标值（决策②检查内置）
        metric_value = evidence_service.add_metric_value(
            db,
            employee_id=record.employee_id,
            metric_code=request.metric_code,
            period="",  # TODO: 从 task 获取
            value=request.value,
            source=request.source,
            as_of=request.collected_at
        )
        
        db.commit()
        
        return {
            "status": "success",
            "metric_id": metric_value.id,
            "metric_code": request.metric_code,
            "value": request.value
        }
    
    except exceptions.AttendanceEvidenceRejected as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=exc.message) from exc
    except exceptions.BaseAppError as exc:
        db.rollback()
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc


# ┌─────────────────────────────────────────────────────┐
# │ 健康检查 + 系统信息                                 │
# └─────────────────────────────────────────────────────┘


@app.get("/v1/health", tags=["Health"])
def health_check() -> dict[str, Any]:
    """健康检查。"""
    return {
        "status": "ok",
        "version": "1.0.0",
        "database": "sqlite" if settings.database_url.startswith("sqlite") else "postgresql",
        "timestamp": datetime.now().isoformat()
    }


@app.get("/v1/info", tags=["Health"])
def system_info() -> dict[str, Any]:
    """系统信息（用于排查）。"""
    return {
        "agents": ["QA", "FillCoach", "Suggestion", "Grounding", "PerformanceExplain", "RankExplain"],
        "rules_version": "1.0.0",
        "database": "sqlite" if settings.database_url.startswith("sqlite") else "postgresql",
        "api_version": "1.0.0",
        "features": {
            "k_anonymity": True,
            "privacy_degradation": True,
            "attendance_rejection": True,
            "model_not_trusted": True
        }
    }


# ┌─────────────────────────────────────────────────────┐
# │ 错误处理中间件                                      │
# └─────────────────────────────────────────────────────┘


@app.exception_handler(exceptions.BaseAppError)
async def app_error_handler(request, exc: exceptions.BaseAppError):
    """处理应用异常（BaseAppError）。"""
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "code": exc.code,
            "message": exc.message,
            "details": exc.details
        }
    )


@app.exception_handler(HTTPException)
async def http_exception_handler(request, exc: HTTPException):
    """处理 HTTP 异常。"""
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "code": "HTTP_ERROR",
            "message": exc.detail,
            "details": {}
        }
    )
