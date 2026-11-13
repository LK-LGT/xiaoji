# 🏗️ 框架完整搭建指南

> 目标：清晰的骨架，你直接填代码
> 状态：P0 + P1 + P2 改动已完成，现在搭建代码框架

---

## 一、现有代码结构

```
backend/app/
├── core/
│   ├── __init__.py
│   ├── config.py         ✅ 配置管理
│   └── db.py             ✅ 数据库会话
│
├── domain/
│   ├── __init__.py
│   ├── enums.py          ✅ 状态枚举（Task/Record/Score）
│   └── models.py         ✅ 8 个表的 ORM 模型
│
├── services/             ✅ 业务逻辑
│   ├── __init__.py
│   ├── scoring.py        ✅ 规则引擎（分数的唯一产地）
│   ├── config_service.py ✅ 决策③检验 + 快照
│   ├── evidence_service.py ✅ 决策②考勤验证
│   └── review_service.py ✅ 考核编排
│
├── main.py               ✅ FastAPI 路由（但不完整）
├── __init__.py
└── db/
    ├── __init__.py
    └── seed.py           ✅ 种子数据
```

---

## 二、需要补全的文件

### 2.1 类型定义层 —— `schemas.py` （新建）

```python
# backend/app/schemas.py
"""请求/响应的 Pydantic 模型（API 契约层）。"""

from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime

class MetricValueRequest(BaseModel):
    """添加指标值的请求."""
    metric_code: str = Field(..., description="指标代码，如 'GMV'")
    value: float = Field(..., description="数值")
    source: str = Field(default="hr_system", description="数据来源")
    evidence_id: Optional[str] = Field(None, description="关联的证据 ID")

class SelfReviewRequest(BaseModel):
    """自评提交的请求."""
    self_review_text: str = Field(..., description="自评文本")
    highlight_evidence_ids: List[str] = Field(default_factory=list, description="重点展示的证据 ID")

class ManagerReviewRequest(BaseModel):
    """主管评分的请求."""
    manager_comment: str = Field(..., description="主管评语")
    confirmed_rating: str = Field(..., description="等级，A/B/C/D")
    confirmed_at: datetime = Field(default_factory=datetime.utcnow)

class ScoreResponse(BaseModel):
    """分数响应（data_quality 格式）."""
    total: float = Field(..., description="0-100 的最终分数")
    per_metric_json: dict = Field(..., description="各指标的转换后得分")
    data_quality: dict = Field(..., description="数据完整度 + 来源")
    trace: dict = Field(..., description="计算链路追踪")

class QAQueryRequest(BaseModel):
    """Q&A 查询的请求（POST）."""
    query_type: str = Field(..., description="self_progress / self_result / colleague_result / peer_performance_distribution")
    target_employee_id: str = Field(..., description="要查询的员工 ID")
    task_id: str = Field(..., description="考核任务 ID")

class QAQueryResponse(BaseModel):
    """Q&A 查询的响应（数据 + 数据质量，不含 AI）."""
    data: dict = Field(..., description="结构化数据")
    data_quality: dict = Field(..., description="数据完整度")
```

**工作量**：30 分钟（你补全所有的 Schema）

---

### 2.2 异常处理层 —— `exceptions.py` （新建）

```python
# backend/app/exceptions.py
"""自定义异常。"""

class BaseAppError(Exception):
    """基类异常."""
    def __init__(self, code: str, message: str, status_code: int = 400):
        self.code = code
        self.message = message
        self.status_code = status_code
        super().__init__(message)

class AttendanceEvidenceRejected(BaseAppError):
    """决策②：考勤证据不能进算分集。"""
    def __init__(self):
        super().__init__(
            code="ATTENDANCE_REJECTED",
            message="考勤异常仅作参考，不能进入评分计算",
            status_code=400
        )

class ModelNotTrusted(BaseAppError):
    """决策⑤：模型无法自行决策权限/等级。"""
    def __init__(self, detail: str):
        super().__init__(
            code="MODEL_NOT_TRUSTED",
            message=f"模型输出不可信：{detail}",
            status_code=400
        )

class PermissionDenied(BaseAppError):
    """权限不足。"""
    def __init__(self, user_id: str, action: str):
        super().__init__(
            code="PERMISSION_DENIED",
            message=f"用户 {user_id} 无权执行 {action}",
            status_code=403
        )

class DataValidationError(BaseAppError):
    """数据验证失败。"""
    def __init__(self, detail: str):
        super().__init__(
            code="VALIDATION_ERROR",
            message=detail,
            status_code=422
        )
```

**工作量**：20 分钟（补全所有场景异常）

---

### 2.3 依赖注入层 —— `dependencies.py` （新建）

```python
# backend/app/dependencies.py
"""FastAPI 依赖注入。"""

from fastapi import Depends, HTTPException
from sqlalchemy.orm import Session
from .core.db import get_db
from .domain.models import Employee

async def get_current_user(token: str, db: Session = Depends(get_db)) -> Employee:
    """
    从 token 提取 employee_id，做权限检查。
    
    关键：来自 HTTP header 的 employee_id 是可信的（L2 鉴权层保证）。
    不信任 AI/前端计算出的 employee_id。
    """
    # TODO: 解析 token，从中获取 employee_id
    # token 来自飞书 OAuth 或内部 JWT
    # 永不相信 request.query_params 或 request.body 里的 employee_id
    pass

def get_direct_reports(user_id: str, db: Session = Depends(get_db)) -> list[str]:
    """获取 user_id 的直属下级。
    
    这是权限检查的关键：
    - self_score 查询：只能查自己
    - colleague_score 查询：只能查直属下级
    - manager_review：只能对直属下级评分
    """
    # TODO: 从 Employee 表查询
    pass

def check_permission(
    user_id: str,
    action: str,  # "view_score", "submit_review", "confirm_rating"
    target_id: str,  # 要操作的目标（员工 ID 或任务 ID）
    db: Session = Depends(get_db)
) -> bool:
    """权限检查函数。
    
    参考 P0-3 权限模型：
    - 权限决策完全由 L2（本层）推导，不让 L3（AI）参与
    - 身份来自 token（可信），不信 URL 参数
    """
    # TODO: 实现权限矩阵
    pass
```

**工作量**：1 小时（实现权限矩阵）

---

### 2.4 API 路由层 —— 补全 `main.py`

现有的 main.py 需要补全以下路由：

```python
# backend/app/main.py
"""FastAPI 应用定义 + 所有路由。"""

from fastapi import FastAPI, Depends, HTTPException
from sqlalchemy.orm import Session
from .core.db import get_db, init_db
from .dependencies import get_current_user, check_permission
from .services import scoring, review_service, evidence_service, config_service
from .domain import enums
from . import schemas
from . import exceptions

app = FastAPI(title="员工绩效管理框架", version="1.0.0")

@app.on_event("startup")
async def startup():
    """启动时初始化数据库."""
    init_db()
    # TODO: 加载配置快照

# ┌─────────────────────────────────────────────────────┐
# │ 路由一：发起考核                                     │
# └─────────────────────────────────────────────────────┘

@app.post("/v1/assessment/tasks", tags=["Assessment"])
async def create_task(
    task_request: dict,  # TODO: 定义 request schema
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    HR 发起考核任务。
    
    - 检查权限：只有 HR 能发起
    - 快照：把当前的 Metric/WeightSet/Rubric 冻结
    - 初始化：为每个被评员工创建 AssessmentRecord
    """
    # TODO: 实现
    pass

# ┌─────────────────────────────────────────────────────┐
# │ 路由二：自评提交                                     │
# └─────────────────────────────────────────────────────┘

@app.post("/v1/assessment/records/{record_id}/self-review", tags=["SelfReview"])
async def submit_self_review(
    record_id: str,
    request: schemas.SelfReviewRequest,
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    员工提交自评。
    
    - 检查权限：只能自评自己
    - 验证：evidence_ids 必须存在且属于该员工
    - 关键：不计算分数（那是主管的事）
    """
    # TODO: 实现
    pass

# ┌─────────────────────────────────────────────────────┐
# │ 路由三：添加指标值                                   │
# └─────────────────────────────────────────────────────┘

@app.post("/v1/assessment/records/{record_id}/metrics", tags=["Metrics"])
async def add_metric_value(
    record_id: str,
    request: schemas.MetricValueRequest,
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    添加指标值（数据源、考勤异常等）。
    
    关键决策②：
    - 考勤异常必须被拒（AttendanceEvidenceRejected）
    - 其他指标正常处理
    
    调用 evidence_service.validate() 检查
    """
    # TODO: 实现
    pass

# ┌─────────────────────────────────────────────────────┐
# │ 路由四：主管评分 + 自动计分                          │
# └─────────────────────────────────────────────────────┘

@app.post("/v1/assessment/records/{record_id}/manager-review", tags=["Review"])
async def submit_manager_review(
    record_id: str,
    request: schemas.ManagerReviewRequest,
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    主管评分。
    
    流程：
    1. 权限检查：只能对直属下级评分
    2. 触发计分：调用 scoring.compute_score()
    3. 返回结果：Score + RatingDraft（如果有 AI）
    
    关键原则③：
    - 分数由规则引擎计算，不信任主管评分覆盖
    - 主管评分作为输入，与其他指标一起加权求和
    """
    # TODO: 实现
    pass

# ┌─────────────────────────────────────────────────────┐
# │ 路由五：确认等级 + 重算分数                          │
# └─────────────────────────────────────────────────────┘

@app.post("/v1/assessment/records/{record_id}/confirm", tags=["Review"])
async def confirm_draft(
    record_id: str,
    request: dict,  # TODO: schema
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    HR 或主管确认等级（可能改 AI 建议）。
    
    关键：
    - 重算分数（用最新的输入重新跑规则引擎）
    - 更新 AssessmentRecord.status → confirmed
    - 写入审计日志
    """
    # TODO: 实现
    pass

# ┌─────────────────────────────────────────────────────┐
# │ 路由六：Q&A 查询（新增 POST）                       │
# └─────────────────────────────────────────────────────┘

@app.post("/v1/api/qa/query", tags=["QA"])
async def query_assessment_data(
    request: schemas.QAQueryRequest,
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    查询考核数据（P0-2 改成 POST）。
    
    查询类型（参考 P0-1）：
    - self_progress：我的月中进度
    - self_result：我的月末成绩
    - colleague_result：某员工的月末成绩（只能看直属下级）
    - peer_performance_distribution：部门级别的等级分布（聚合，不排名）
    
    响应格式（参考 P0-5）：
    - data：结构化数据
    - data_quality：完整度 + 来源 + 降级标记
    - 不含 score 字段（数字不暴露）
    """
    # TODO: 实现权限检查 + 调用查询服务
    pass

# ┌─────────────────────────────────────────────────────┐
# │ 路由七：健康检查 + 版本                             │
# └─────────────────────────────────────────────────────┘

@app.get("/v1/health", tags=["Health"])
async def health_check():
    """健康检查。"""
    return {"status": "ok", "version": "1.0.0"}

@app.get("/v1/info", tags=["Health"])
async def get_info():
    """系统信息（用于排查）。"""
    return {
        "agents": ["QA", "FillCoach", "Suggestion", "Grounding", "PerformanceExplain", "RankExplain"],
        "rules_version": "1.0.0",
        "database": "sqlite" if settings.database_url.startswith("sqlite") else "postgresql",
    }
```

**工作量**：2-3 小时（实现所有路由）

---

### 2.5 服务层补全 —— `qa_service.py` （新建）

```python
# backend/app/services/qa_service.py
"""Q&A 查询服务（参考 P0-1/2/4）。"""

from sqlalchemy.orm import Session
from ..domain import models, enums
from .. import exceptions

def query_self_progress(
    employee_id: str,
    task_id: str,
    db: Session
) -> dict:
    """
    查询自己的月中进度。
    
    响应格式（参考 P0-1）：
    {
        "as_of": "2026-10-31",
        "completed_weight": 0.8,      # 已出数权重
        "missing_weight": 0.2,        # 缺失权重
        "per_metric": {
            "GMV": {"value": 1250000, "score": 85},
            ...
        }
    }
    """
    # TODO: 实现
    pass

def query_self_result(
    employee_id: str,
    task_id: str,
    db: Session
) -> dict:
    """
    查询自己的月末成绩（需要主管确认过）。
    
    响应格式（参考 P0-1）：
    {
        "status": "confirmed",
        "confirmed_rating": "A",
        "confirmed_by": "M001",
        "confirmed_at": "2026-11-10T18:00:00",
        "score": {...},
        "appeal_deadline": "2026-11-15"
    }
    """
    # TODO: 实现
    pass

def query_department_summary(
    department_id: str,
    task_id: str,
    db: Session
) -> dict:
    """
    查询部门汇总（参考 P2-1 k-匿名）。
    
    关键：
    - 如果部门 < 5 人，average_score 返回 null
    - 显示等级分布（A: 3 人, B: 5 人, ...）
    """
    # TODO: 实现
    pass
```

**工作量**：1 小时（实现 3 个查询）

---

## 三、完整的文件创建清单

```
需要新建的文件：

1. backend/app/schemas.py          （Pydantic 模型，30 min）
   ├─ MetricValueRequest
   ├─ SelfReviewRequest
   ├─ ManagerReviewRequest
   ├─ ScoreResponse
   ├─ QAQueryRequest
   └─ QAQueryResponse

2. backend/app/exceptions.py        （自定义异常，20 min）
   ├─ BaseAppError
   ├─ AttendanceEvidenceRejected
   ├─ ModelNotTrusted
   ├─ PermissionDenied
   └─ DataValidationError

3. backend/app/dependencies.py       （权限注入，1 hour）
   ├─ get_current_user()
   ├─ get_direct_reports()
   └─ check_permission()

4. backend/app/services/qa_service.py （查询服务，1 hour）
   ├─ query_self_progress()
   ├─ query_self_result()
   └─ query_department_summary()

5. backend/app/main.py              （补全路由，2-3 hours）
   ├─ POST /v1/assessment/tasks
   ├─ POST /v1/assessment/records/{id}/self-review
   ├─ POST /v1/assessment/records/{id}/metrics
   ├─ POST /v1/assessment/records/{id}/manager-review
   ├─ POST /v1/assessment/records/{id}/confirm
   ├─ POST /v1/api/qa/query        ← 新的 POST（P0-2）
   └─ GET /v1/health

已有（无需改）：
- backend/app/core/db.py
- backend/app/core/config.py
- backend/app/domain/models.py
- backend/app/domain/enums.py
- backend/app/services/scoring.py
- backend/app/services/config_service.py
- backend/app/services/evidence_service.py
- backend/app/services/review_service.py
```

---

## 四、实现顺序（推荐）

### 第 1 阶段（30 分钟）—— 类型定义

1. 创建 `schemas.py`
2. 定义所有 Request/Response 类

✅ **交付物**：API 契约清晰

### 第 2 阶段（20 分钟）—— 异常处理

1. 创建 `exceptions.py`
2. 定义所有业务异常

✅ **交付物**：错误处理统一

### 第 3 阶段（1 小时）—— 权限注入

1. 创建 `dependencies.py`
2. 实现 `get_current_user()` 和权限检查

✅ **交付物**：权限模型落地（P0-3）

### 第 4 阶段（1 小时）—— Q&A 查询

1. 创建 `qa_service.py`
2. 实现 3 个查询函数

✅ **交付物**：查询能力（P0-1）

### 第 5 阶段（2-3 小时）—— API 路由

1. 改 `main.py`，补全 7 个路由
2. 添加错误处理中间件
3. 端到端测试

✅ **交付物**：完整的 API（P0-2/3/4/5）

---

## 五、框架使用说明

当你实现每个模块时，遵循以下原则：

### 原则①：权限来自 token，不信 URL/Body

```python
# ✅ 正确
current_user = get_current_user(token)  # 从 token 提取，可信
employee_id = current_user.id
direct_reports = get_direct_reports(employee_id, db)
# 权限检查：target_id 必须在 direct_reports 里

# ❌ 错误
employee_id = request.query_params["employee_id"]  # 前端伪造
target_id = request.body["target_employee_id"]     # AI 建议
# 永远不信这些
```

### 原则②：考勤异常被拒绝

```python
# ✅ 正确
if metric.usage == "reference_only":
    raise exceptions.AttendanceEvidenceRejected()

# ❌ 错误
metric_value = metric.value  # 考勤异常照样存
```

### 原则③：分数只由规则引擎产生

```python
# ✅ 正确
score = scoring.compute_score(record, db)  # L2 规则引擎
# 不接受 AI/主管的 score 字段

# ❌ 错误
score = request.body["score"]  # 信任 AI 或主管
```

### 原则④：响应不含 score 字段

```python
# ✅ 正确
response = {
    "data": {...},
    "data_quality": {
        "completeness": 0.95,
        "as_of": "2026-10-31",
        "citations": ["Score.total", "MetricValue.gmv"]
    }
}

# ❌ 错误
response = {
    "score": 78.5,  # 不暴露数字
    ...
}
```

---

## 六、测试用例框架

```python
# backend/tests/test_new_routes.py
"""新路由的测试。"""

import pytest
from fastapi.testclient import TestClient
from ..app.main import app
from ..app.core.db import get_db, get_sessionmaker

@pytest.fixture
def client():
    """创建测试客户端。"""
    # 使用内存 SQLite
    # 初始化表和种子数据
    return TestClient(app)

def test_create_task(client):
    """测试发起考核。"""
    response = client.post(
        "/v1/assessment/tasks",
        json={...},
        headers={"Authorization": "Bearer test_token"}
    )
    assert response.status_code == 200

def test_qa_query_permission(client):
    """测试 Q&A 查询权限。"""
    # 员工 A 不能查员工 B 的数据
    # 主管才能查
    pass

def test_attendance_rejected(client):
    """测试考勤异常被拒。"""
    # 添加考勤异常时应抛 AttendanceEvidenceRejected
    pass
```

---

## 七、运行检查清单

```
框架搭建完成后，运行以下检查：

[ ] 1. 导入检查
    python -c "from backend.app import schemas, exceptions, dependencies"

[ ] 2. 数据库初始化
    python -c "from backend.app.core.db import init_db; init_db()"

[ ] 3. 应用启动
    cd backend && python -m uvicorn app.main:app --port 8000

[ ] 4. 健康检查
    curl http://localhost:8000/v1/health

[ ] 5. 测试运行
    pytest backend/tests/ -v

[ ] 6. 回归测试（P0-6 的 29 个测试）
    pytest backend/tests/test_framework.py -v
    应该全部 PASSED
```

---

## 八、下一步

1. 创建上述 5 个文件（框架搭建）
2. 填入你的业务代码
3. 运行测试

我会先生成所有的**框架代码** + **注释详细的模板**。你只需要**填入 TODO 部分**。

准备好了吗？
