# 【板块1】快速参考卡片

## 🎯 核心函数速查表

### 1. `get_current_user(authorization, db)` - 获取当前用户

```python
# 使用
user = await get_current_user(authorization="Bearer emp_token_001", db=db)
# → Employee(id="E001", name="刘刚", ...)

# 异常
NotAuthenticated("缺少 Authorization header")
NotAuthenticated("Authorization header 格式错误")
NotAuthenticated("Token 无效或已过期")
```

---

### 2. `get_direct_reports(user, db)` - 查询直属下级

```python
# 使用
reports = get_direct_reports(user=m001, db=db)
# → ["E001", "E002"]

# 或 FastAPI 依赖
@app.get("/subordinates")
async def list_subs(user = Depends(get_current_user), db = Depends(get_db)):
    reports = get_direct_reports(user, db)
```

---

### 3. `check_permission(user, action)` - 权限矩阵检查

```python
# 使用
has_perm = check_permission(user=e001, action="view_own_score")
# → True

# 权限列表
actions = [
    "view_own_score",              # 查看自己的成绩
    "view_direct_report_score",    # 查看下级的成绩
    "view_all_scores",             # 查看所有人的成绩
    "submit_own_review",           # 提交自己的自评
    "submit_direct_report_review", # 提交下级的评分
    "confirm_rating",              # 确认最终等级
    "add_metric_to_record",        # 添加指标
    "view_audit_log",              # 查看操作日志
]
```

---

### 4. `check_can_view_score(user, target_employee_id, db)` - 查看成绩权限

```python
# 使用
try:
    check_can_view_score(user=user, target_employee_id="E001", db=db)
    # 业务逻辑
except PermissionDenied as e:
    # 处理拒绝：403 Forbidden
    raise HTTPException(status_code=403, detail=e.message)

# 返回
# True - 有权
# 异常 - 无权

# 规则
# 自己 → 检查 view_own_score
# 下级 → 检查 view_direct_report_score + manager_id 验证
# 任何人 → 检查 view_all_scores (HR)
```

---

### 5. `check_can_submit_review(user, target_record_id, db)` - 提交评分权限

```python
# 使用
try:
    record = check_can_submit_review(user=user, target_record_id="RECORD_001", db=db)
    # record 是 AssessmentRecord 对象
    # 业务逻辑
except PermissionDenied:
    raise HTTPException(status_code=403)
except RecordNotFound:
    raise HTTPException(status_code=404)

# 返回
# AssessmentRecord - 有权
# 异常 - 无权

# 规则
# 自己的 → 检查 submit_own_review
# 下级的 → 检查 submit_direct_report_review + 关系验证
```

---

## 📋 Mock Token 速查

| Token | 用户 | 角色 | 下级 |
|-------|------|------|------|
| `emp_token_001` | E001 | 普通员工 | - |
| `emp_token_002` | E002 | 普通员工 | - |
| `emp_token_003` | E003 | 普通员工 | - |
| `manager_token_001` | M001 | 主管 | E001, E002 |
| `manager_token_002` | M002 | 主管 | M001, E003 |
| `hr_token_001` | HR001 | HR | - |

---

## 🔐 权限速查表

```
┌───────────┬────────────┬──────────┬──────────┐
│  操作     │ 普通员工   │ 主管     │ HR       │
├───────────┼────────────┼──────────┼──────────┤
│ 查看自己  │ ✓          │ ✓        │ ✓        │
│ 查看下级  │ ✗          │ ✓        │ ✓        │
│ 查看所有  │ ✗          │ ✗        │ ✓        │
│ 自己自评  │ ✓          │ ✓        │ ✓        │
│ 下级评分  │ ✗          │ ✓        │ ✓        │
│ 确认等级  │ ✗          │ ✗        │ ✓        │
│ 添加指标  │ ✗          │ ✓        │ ✓        │
│ 查看日志  │ ✗          │ ✗        │ ✓        │
└───────────┴────────────┴──────────┴──────────┘
```

---

## 🛡️ 异常速查

| 异常 | 何时抛出 | HTTP 状态 | 何处处理 |
|------|--------|---------|--------|
| `NotAuthenticated` | Token 无效/缺少 | 401 | 认证中间件 |
| `PermissionDenied` | 无权执行操作 | 403 | 业务层 |
| `RecordNotFound` | 记录不存在 | 404 | 业务层 |

---

## 💡 FastAPI 路由模板

### 模板 1：自己查看自己

```python
@app.get("/api/score")
async def my_score(
    user: models.Employee = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    # 自动有权（自己查自己）
    score = db.query(Score).filter_by(employee_id=user.id).first()
    return score
```

### 模板 2：查看他人需权限检查

```python
@app.get("/api/score/{employee_id}")
async def view_score(
    employee_id: str,
    user: models.Employee = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    # 权限检查
    check_can_view_score(user, employee_id, db)
    
    score = db.query(Score).filter_by(employee_id=employee_id).first()
    return score
```

### 模板 3：主管专用接口

```python
@app.get("/api/subordinates")
async def list_subordinates(
    user: models.Employee = Depends(get_current_manager),  # 自动检查
    db: Session = Depends(get_db)
):
    # 到这里已经确保是主管
    reports = get_direct_reports(user, db)
    employees = db.query(Employee).filter(Employee.id.in_(reports)).all()
    return employees
```

### 模板 4：HR 专用接口

```python
@app.post("/api/admin/confirm-rating/{record_id}")
async def confirm_rating(
    record_id: str,
    rating: str,
    user: models.Employee = Depends(get_current_hr),  # 自动检查
    db: Session = Depends(get_db)
):
    # 到这里已经确保是 HR
    record = db.query(AssessmentRecord).filter_by(id=record_id).first()
    record.confirmed_rating = rating
    db.commit()
    return record
```

---

## 🧪 测试命令速查

```bash
# 快速验证（5 秒）
python BLOCK1_QUICK_TEST.py

# 运行所有单元测试
pytest backend/tests/test_dependencies.py -v

# 运行特定测试类
pytest backend/tests/test_dependencies.py::TestGetCurrentUser -v

# 运行特定测试
pytest backend/tests/test_dependencies.py::TestGetCurrentUser::test_valid_token_ordinary_employee -v

# 生成覆盖率报告
pytest backend/tests/test_dependencies.py --cov=backend.app.dependencies --cov-report=html
```

---

## 🚀 快速集成（3 步）

### Step 1: 导入依赖

```python
from backend.app.dependencies import (
    get_current_user,
    get_current_hr,
    get_current_manager,
    check_can_view_score,
    check_can_submit_review,
)
```

### Step 2: 添加权限检查

```python
@app.get("/api/score/{employee_id}")
async def view_score(
    employee_id: str,
    user: models.Employee = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    check_can_view_score(user, employee_id, db)  # ← 添加这一行
    # ... 业务逻辑
```

### Step 3: 测试

```bash
curl -H "Authorization: Bearer emp_token_001" \
     http://localhost:8000/api/score/E001  # ✅ 允许

curl -H "Authorization: Bearer emp_token_001" \
     http://localhost:8000/api/score/E002  # ❌ 拒绝
```

---

## ⚠️ 常见错误

### 错误 1：忘记权限检查

```python
# ❌ 错误
@app.get("/api/score/{employee_id}")
async def view_score(employee_id: str, ...):
    return db.query(Score).filter_by(employee_id=employee_id).first()

# ✅ 正确
@app.get("/api/score/{employee_id}")
async def view_score(employee_id: str, user: models.Employee = Depends(get_current_user), db: Session = Depends(get_db)):
    check_can_view_score(user, employee_id, db)  # 添加权限检查
    return db.query(Score).filter_by(employee_id=employee_id).first()
```

### 错误 2：从 URL 参数获取 user_id

```python
# ❌ 错误（不信任 URL 参数）
@app.get("/api/score")
async def view_score(employee_id: str = Query(...)):  # ← 不要这样做
    return db.query(Score).filter_by(employee_id=employee_id).first()

# ✅ 正确（从 token 解析）
@app.get("/api/score")
async def view_score(user: models.Employee = Depends(get_current_user)):
    employee_id = user.id  # 从 token 获取
    return db.query(Score).filter_by(employee_id=employee_id).first()
```

### 错误 3：忘记异常处理

```python
# ❌ 错误（异常未处理）
@app.get("/api/score/{employee_id}")
async def view_score(employee_id: str, user = Depends(get_current_user), db = Depends(get_db)):
    check_can_view_score(user, employee_id, db)  # 如果异常未处理，返回 500
    return db.query(Score).filter_by(employee_id=employee_id).first()

# ✅ 正确（异常已处理或通过中间件处理）
@app.exception_handler(PermissionDenied)
async def permission_denied_handler(request, exc):
    return JSONResponse(status_code=403, content={...})
```

---

## 📞 技术支持

- 快速验证脚本：`BLOCK1_QUICK_TEST.py`
- 使用示例：`BLOCK1_USAGE_EXAMPLE.py`
- 完整文档：`BLOCK1_CHECKLIST.md`
- 总结报告：`BLOCK1_SUMMARY.md`
- 单元测试：`backend/tests/test_dependencies.py`

**有问题？运行快速验证脚本快速诊断！**
