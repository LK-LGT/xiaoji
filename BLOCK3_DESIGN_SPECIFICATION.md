# 【板块3】自评和评分接口 - 完整设计方案

**设计时间**：2026-11-12 12:31 UTC+8  
**目标**：实现员工自评和主管评分的两个核心接口  
**预计工作量**：4-6 小时（2 个接口 + 3 步验证）

---

## 📋 板块3 概览

### 核心业务逻辑

```
【流程图】
┌─────────────────────────────────────────┐
│  【板块2】创建考核任务                   │
│  POST /v1/assessment/tasks               │
│  ↓ 生成 AssessmentRecord (status=not_started)
│
├─────────────────────────────────────────┤
│  【板块3.1】员工自评 ← 你在这里            │
│  POST /v1/assessment/records/{id}/      │
│      self-review                         │
│  ↓ Record.status: not_started → reviewing
│
├─────────────────────────────────────────┤
│  【板块3.2】主管评分                    │
│  POST /v1/assessment/records/{id}/      │
│      manager-review                     │
│  ↓ Record.status: reviewing → scored
│
├─────────────────────────────────────────┤
│  【板块4】规则引擎计算最终得分          │
│  POST /v1/scores/calculate               │
│  ↓ 生成最终评分
│
└─────────────────────────────────────────┘
```

### 关键特性

1. **权限隔离**
   - 员工只能修改自己的自评
   - 主管只能修改下属的评分

2. **状态管理**
   - not_started → reviewing → scored → finalized

3. **数据冻结**
   - 一旦提交后不能修改（使用 submit_locked）

4. **时间控制**
   - 不能超过截止时间
   - 自评和评分有不同的截止时间

---

## 🔧 【板块3.1】员工自评接口

### API 定义

```
接口名称：员工提交自评表单
HTTP 方法：POST
端点：/v1/assessment/records/{record_id}/self-review
认证：需要 Token（员工身份）
```

### 请求 Schema

```python
class SelfReviewRequest(BaseModel):
    """自评表单请求"""
    
    # 1. 自评分数（1-5 分）
    self_score: int = Field(ge=1, le=5)
    
    # 2. 自评评论（自由文本）
    self_comment: str = Field(min_length=10, max_length=2000)
    
    # 3. 自评指标值（可选，用于自填指标）
    metric_values: Optional[List[MetricValueRequest]] = None
    
    # 4. 自评亮点（可选）
    highlights: Optional[str] = None
    
    # 5. 改进方向（可选）
    improvement_areas: Optional[str] = None
```

### 响应 Schema

```python
class SelfReviewResponse(BaseModel):
    """自评提交成功响应"""
    
    record_id: str = "REC_001"
    employee_id: str = "E001"
    status: str = "reviewing"
    
    # 自评数据
    self_score: int = 4
    self_comment: str = "..."
    self_submitted_at: datetime = "2026-11-12T12:30:00"
    
    # 截止时间
    self_review_deadline: datetime
    manager_review_deadline: datetime
```

### 业务逻辑

```python
@router.post("/v1/assessment/records/{record_id}/self-review")
async def submit_self_review(
    record_id: str,
    request: SelfReviewRequest,
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    步骤 1：权限校验
    ├─ Token 有效性检查
    └─ 身份是否为员工（非 HR、非主管）
    
    步骤 2：数据查询
    ├─ 获取 AssessmentRecord
    ├─ 获取相关的 Task
    └─ 获取 ConfigSnapshot（冻结的配置）
    
    步骤 3：参数验证
    ├─ record_id 是否存在
    ├─ 员工 ID 是否为当前用户
    ├─ 自评分数范围 [1-5]
    ├─ 自评评论长度 [10-2000]
    ├─ 指标值是否有效
    └─ 是否超过截止时间
    
    步骤 4：状态检查
    ├─ Record.status 是否为 "not_started"
    ├─ Record.submit_locked 是否为 False
    └─ 是否已经提交过（判断逻辑）
    
    步骤 5：业务逻辑
    ├─ 验证指标值（如有）
    ├─ 保存自评内容
    ├─ 更新 Record.status = "reviewing"
    ├─ 设置 Record.self_submitted_at
    └─ 设置 Record.submit_locked = True
    
    步骤 6：异常处理
    ├─ PermissionDenied: 非员工或不是自己的评估
    ├─ DataValidationError: 参数不合法
    ├─ BusinessLogicError: 超过截止时间
    └─ ConflictError: 已经提交过
    
    步骤 7：事务提交
    └─ db.commit()
    
    步骤 8：响应返回
    └─ SelfReviewResponse
    """
```

### 核心验证点

| 验证项 | 规则 | HTTP 状态 |
|------|------|---------|
| 无效 token | Token 过期/无效 | 401 |
| 非员工 | 是 HR/管理员 | 403 |
| 不是自己 | employee_id ≠ current_user.id | 403 |
| Record 不存在 | 查询返回 None | 404 |
| 已提交过 | submit_locked == True | 409 |
| 超过截止时间 | now() > self_review_deadline | 422 |
| 分数无效 | score ∉ [1-5] | 422 |
| 评论太短 | len(comment) < 10 | 422 |
| 状态错误 | status ≠ "not_started" | 409 |

### 错误响应示例

```python
# 错误 1：超过截止时间
{
    "code": "DEADLINE_EXCEEDED",
    "message": "自评截止时间已过（截止于 2026-11-20 23:59:59）",
    "status_code": 422
}

# 错误 2：已经提交过
{
    "code": "ALREADY_SUBMITTED",
    "message": "您已经提交过自评，不能重复提交",
    "status_code": 409
}

# 错误 3：权限不足
{
    "code": "PERMISSION_DENIED",
    "message": "您无权修改其他员工的评估记录",
    "status_code": 403
}

# 错误 4：参数无效
{
    "code": "VALIDATION_ERROR",
    "message": "自评分数必须在 1-5 之间",
    "status_code": 422
}
```

---

## 🔧 【板块3.2】主管评分接口

### API 定义

```
接口名称：主管提交员工评分
HTTP 方法：POST
端点：/v1/assessment/records/{record_id}/manager-review
认证：需要 Token（主管/HR 身份）
```

### 请求 Schema

```python
class ManagerReviewRequest(BaseModel):
    """主管评分请求"""
    
    # 1. 指标值数组（必需）
    metric_values: List[MetricValueRequest]
    # ├─ metric_code: str
    # ├─ value: float
    # ├─ unit: str
    # └─ comment: str (可选)
    
    # 2. 主管评分（1-5 分）
    manager_score: int = Field(ge=1, le=5)
    
    # 3. 主管评论（自由文本）
    manager_comment: str = Field(min_length=10, max_length=2000)
    
    # 4. 评分理由（可选）
    rating_reason: Optional[str] = None
    
    # 5. 建议行动（可选）
    recommended_action: Optional[str] = None
```

### 响应 Schema

```python
class ManagerReviewResponse(BaseModel):
    """主管评分提交成功响应"""
    
    record_id: str
    employee_id: str
    status: str = "scored"
    
    # 主管评分数据
    manager_score: int = 4
    manager_comment: str = "..."
    manager_submitted_at: datetime
    
    # 指标值摘要
    metric_values_count: int
    preliminary_score: Optional[float] = None  # 初步计算的总分
    
    # 下一步
    next_step: str = "等待 HR 最终确认或规则引擎计算"
```

### 业务逻辑

```python
@router.post("/v1/assessment/records/{record_id}/manager-review")
async def submit_manager_review(
    record_id: str,
    request: ManagerReviewRequest,
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    步骤 1：权限校验
    ├─ Token 有效性检查
    ├─ 身份是否为主管或 HR
    └─ 是否是该员工的主管
    
    步骤 2：数据查询
    ├─ 获取 AssessmentRecord
    ├─ 获取被评员工 Employee
    ├─ 获取相关的 Task
    └─ 获取 ConfigSnapshot（冻结的配置）
    
    步骤 3：参数验证
    ├─ record_id 是否存在
    ├─ 当前用户是否为该员工的主管
    ├─ 指标值列表是否有效
    ├─ 指标值中的 metric_code 是否有效
    ├─ 指标值的数值是否在合理范围
    ├─ 主管评分 [1-5]
    ├─ 评论长度 [10-2000]
    └─ 是否超过截止时间
    
    步骤 4：状态检查
    ├─ Record.status 是否为 "reviewing"
    │  └─ 说明：只有员工完成自评后，主管才能评分
    ├─ Record.submit_locked 是否为 False
    └─ 员工是否已经完成自评（self_submitted_at != None）
    
    步骤 5：指标值验证
    ├─ 指标代码是否存在于 ConfigSnapshot
    ├─ 指标值是否超出合理范围
    ├─ 是否缺少必需的指标
    └─ 单位是否匹配
    
    步骤 6：业务逻辑
    ├─ 保存所有指标值到 MetricValue 表
    ├─ 计算初步得分（基于权重）
    ├─ 保存主管评论和评分
    ├─ 更新 Record.status = "scored"
    ├─ 设置 Record.manager_submitted_at
    ├─ 设置 Record.confirmed_rating = initial_score
    └─ 设置 Record.submit_locked = True
    
    步骤 7：异常处理
    ├─ PermissionDenied: 不是主管
    ├─ DataValidationError: 参数不合法
    ├─ BusinessLogicError: 超过截止时间
    ├─ ConflictError: 员工未完成自评
    └─ ConflictError: 已经评分过
    
    步骤 8：事务提交
    └─ db.commit()
    
    步骤 9：响应返回
    └─ ManagerReviewResponse
    """
```

### 核心验证点

| 验证项 | 规则 | HTTP 状态 |
|------|------|---------|
| 无效 token | Token 过期/无效 | 401 |
| 非主管 | 不是该员工的主管 | 403 |
| Record 不存在 | 查询返回 None | 404 |
| 员工未自评 | self_submitted_at == None | 409 |
| 已评分过 | submit_locked == True | 409 |
| 超过截止时间 | now() > manager_review_deadline | 422 |
| 指标值缺失 | 缺少必需指标 | 422 |
| 指标值无效 | value 超出范围 | 422 |
| 评分无效 | score ∉ [1-5] | 422 |
| 状态错误 | status ≠ "reviewing" | 409 |

### 错误响应示例

```python
# 错误 1：员工未完成自评
{
    "code": "EMPLOYEE_NOT_REVIEWED",
    "message": "员工尚未完成自评，无法进行主管评分",
    "status_code": 409
}

# 错误 2：指标值缺失
{
    "code": "MISSING_METRICS",
    "message": "缺少必需的指标值：GMV, 留任率",
    "status_code": 422
}

# 错误 3：不是员工主管
{
    "code": "PERMISSION_DENIED",
    "message": "您无权为该员工评分（不是其主管）",
    "status_code": 403
}

# 错误 4：指标值范围错误
{
    "code": "INVALID_METRIC_VALUE",
    "message": "GMV 值 150 超出合理范围 [0-100]",
    "status_code": 422
}
```

---

## 📊 数据流图

### 自评流程

```
员工查看考核任务
    ↓
点击"提交自评"
    ↓
填写自评表单
├─ 自评分数 (1-5)
├─ 自评评论 (10-2000 字)
├─ 指标值（可选）
└─ 亮点/改进
    ↓
调用 POST /v1/assessment/records/{id}/self-review
    ↓
服务器验证
├─ 权限检查 ✓
├─ 参数验证 ✓
├─ 截止时间检查 ✓
└─ 状态检查 ✓
    ↓
保存自评数据
├─ SelfReview 表
├─ MetricValue 表（如有）
└─ AssessmentRecord.status = "reviewing"
    ↓
返回 200 OK
    ↓
前端显示"自评已提交"
    ↓
主管看到可以开始评分
```

### 评分流程

```
主管查看待评分员工列表
    ↓
选择员工点击"提交评分"
    ↓
填写评分表单
├─ 输入指标值（GMV、留任率等）
├─ 主管评分 (1-5)
├─ 主管评论 (10-2000 字)
└─ 评分理由/建议
    ↓
调用 POST /v1/assessment/records/{id}/manager-review
    ↓
服务器验证
├─ 权限检查 ✓（是否是主管）
├─ 参数验证 ✓（指标值有效）
├─ 截止时间检查 ✓
├─ 状态检查 ✓（员工已自评）
└─ 指标值完整性检查 ✓
    ↓
保存评分数据
├─ ManagerReview 表
├─ MetricValue 表
├─ AssessmentRecord.status = "scored"
└─ AssessmentRecord.confirmed_rating = initial_score
    ↓
返回 200 OK + 初步得分
    ↓
前端显示"评分已提交"
    ↓
系统准备进入【板块4】规则引擎阶段
```

---

## 🗂️ 数据库表设计

### SelfReview 表

```python
class SelfReview(Base):
    __tablename__ = "self_review"
    
    id = Column(UUID, primary_key=True, default=uuid4)
    record_id = Column(String, ForeignKey("assessment_record.id"))
    employee_id = Column(String, ForeignKey("employee.id"))
    
    # 自评分数
    self_score = Column(Integer)  # 1-5
    
    # 自评评论
    self_comment = Column(Text)  # 10-2000 字
    
    # 自评亮点和改进
    highlights = Column(Text, nullable=True)
    improvement_areas = Column(Text, nullable=True)
    
    # 时间戳
    created_at = Column(DateTime, default=datetime.now)
    submitted_at = Column(DateTime, nullable=True)
    updated_at = Column(DateTime, default=datetime.now)
```

### ManagerReview 表

```python
class ManagerReview(Base):
    __tablename__ = "manager_review"
    
    id = Column(UUID, primary_key=True, default=uuid4)
    record_id = Column(String, ForeignKey("assessment_record.id"))
    manager_id = Column(String, ForeignKey("employee.id"))
    
    # 主管评分
    manager_score = Column(Integer)  # 1-5
    
    # 主管评论
    manager_comment = Column(Text)  # 10-2000 字
    
    # 评分理由
    rating_reason = Column(Text, nullable=True)
    recommended_action = Column(Text, nullable=True)
    
    # 初步计算的总分
    preliminary_score = Column(Float, nullable=True)
    
    # 时间戳
    created_at = Column(DateTime, default=datetime.now)
    submitted_at = Column(DateTime, nullable=True)
    updated_at = Column(DateTime, default=datetime.now)
```

### MetricValue 表（如果还没有）

```python
class MetricValue(Base):
    __tablename__ = "metric_value"
    
    id = Column(UUID, primary_key=True, default=uuid4)
    record_id = Column(String, ForeignKey("assessment_record.id"))
    metric_code = Column(String)  # 如 "GMV", "retention_rate"
    
    # 数值
    value = Column(Float)
    unit = Column(String)
    
    # 来源（self_review 或 manager_review）
    source = Column(String)  # "self" 或 "manager"
    
    # 备注
    comment = Column(Text, nullable=True)
    
    # 时间戳
    created_at = Column(DateTime, default=datetime.now)
```

---

## ✅ 3 步验证计划

### 第一步：代码自查（1-2h）

```python
✅ 权限验证
├─ Depends(get_current_user) 使用
├─ 是否只有员工能调用自评接口
├─ 是否只有主管能调用评分接口
└─ 跨部门权限检查

✅ 业务逻辑
├─ 参数验证 7 层
├─ 状态转移是否正确
├─ 截止时间检查
├─ 重复提交检查（submit_locked）
└─ 指标值有效性

✅ 异常处理
├─ 5+ 种异常类型
├─ 错误消息清晰
└─ HTTP 状态码正确

✅ 数据一致性
├─ 事务提交/回滚
├─ 外键约束
└─ 状态更新原子性
```

### 第二步：pytest 单元测试（1-2h）

```python
✅ 参数验证测试
├─ test_empty_comment
├─ test_invalid_score
├─ test_invalid_metrics
└─ test_expired_deadline

✅ 权限测试
├─ test_non_employee_cannot_self_review
├─ test_non_manager_cannot_manager_review
├─ test_cross_department_rejection
└─ test_self_employee_only

✅ 业务逻辑测试
├─ test_successful_self_review
├─ test_successful_manager_review
├─ test_state_transition
└─ test_cannot_resubmit

✅ 异常处理测试
├─ test_deadline_exceeded
├─ test_employee_not_reviewed_yet
└─ test_already_submitted
```

### 第三步：HTTP 集成测试（1-2h）

```bash
# 测试 1：员工自评成功
curl -X POST http://localhost:8000/v1/assessment/records/REC_001/self-review \
  -H "Authorization: Bearer emp_token" \
  -H "Content-Type: application/json" \
  -d '{
    "self_score": 4,
    "self_comment": "我认为完成了年度目标的 85%...",
    "highlights": "积极推进新产品方案"
  }'
# 预期：200 OK + record_id + status="reviewing"

# 测试 2：主管评分成功
curl -X POST http://localhost:8000/v1/assessment/records/REC_001/manager-review \
  -H "Authorization: Bearer manager_token" \
  -H "Content-Type: application/json" \
  -d '{
    "metric_values": [
      {"metric_code": "GMV", "value": 95, "unit": "万元"},
      {"metric_code": "retention_rate", "value": 92, "unit": "%"}
    ],
    "manager_score": 4,
    "manager_comment": "表现良好，完成了目标..."
  }'
# 预期：200 OK + status="scored" + preliminary_score

# 测试 3：权限拒绝 - 非主管
curl -X POST http://localhost:8000/v1/assessment/records/REC_001/manager-review \
  -H "Authorization: Bearer emp_token"
# 预期：403 PERMISSION_DENIED

# 测试 4：状态错误 - 员工未自评
# （直接调用主管评分接口而员工还没自评）
# 预期：409 CONFLICT + "员工未完成自评"

# 测试 5：参数错误 - 超出分数范围
curl -X POST http://localhost:8000/v1/assessment/records/REC_001/self-review \
  -d '{"self_score": 10, ...}'
# 预期：422 VALIDATION_ERROR
```

---

## 📈 预期工作量

| 阶段 | 任务 | 预计时间 |
|------|------|--------|
| 代码 | 【板块3.1】自评接口实现 | 1.5h |
| | 【板块3.2】评分接口实现 | 1.5h |
| 测试 | 单元测试编写 | 1.5h |
| | HTTP 集成测试 | 1h |
| 文档 | API 文档 + 验收报告 | 0.5h |
| **总计** | | **6h** |

---

## 🚀 启动命令

```bash
# 1. 创建代码文件
touch backend/app/routes/v1/assessment_records.py

# 2. 创建 schema
touch backend/app/schemas_block3.py

# 3. 启动 API
python -m uvicorn backend.app.main:app --reload

# 4. 运行测试
python -m pytest backend/app/tests/test_block3_self_review.py -v
python -m pytest backend/app/tests/test_block3_manager_review.py -v
```

---

**准备好开始【板块3】的实现了吗？** 🚀

