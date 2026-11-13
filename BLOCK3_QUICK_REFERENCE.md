# 【板块3】快速参考 - 5 分钟速读版

**这是什么**：自评和评分接口  
**为什么**：完整的评分流程需要员工参与  
**怎么做**：两个 POST 接口 + 权限隔离 + 状态管理

---

## 🎯 核心概念

### 板块3 的两个接口

#### 接口 1️⃣：员工自评
```
POST /v1/assessment/records/{record_id}/self-review

谁调用：员工
做什么：提交自评分数、评论、建议
返回什么：record_id、status="reviewing"
权限检查：当前用户 == 被评人员
```

#### 接口 2️⃣：主管评分
```
POST /v1/assessment/records/{record_id}/manager-review

谁调用：主管或 HR
做什么：提交指标值、评分、评论
返回什么：record_id、status="scored"、preliminary_score
权限检查：当前用户 == 被评人的主管
```

---

## 📊 状态流转

```
AssessmentRecord 的生命周期

not_started (初始)
    ↓ [员工调用 self-review]
reviewing (评审中)
    ↓ [主管调用 manager-review]
scored (已评分)
    ↓ [规则引擎或 HR 确认]
finalized (最终确定)
```

---

## ✅ 关键验证清单

### 自评接口验证

| # | 验证项 | 失败返回 |
|---|------|--------|
| 1 | Token 有效 | 401 |
| 2 | 当前用户是员工 | 403 |
| 3 | record_id 存在 | 404 |
| 4 | 当前用户 == 被评人 | 403 |
| 5 | 未超过截止时间 | 422 |
| 6 | 分数 ∈ [1,5] | 422 |
| 7 | 评论长度 ∈ [10,2000] | 422 |
| 8 | 未提交过（submit_locked=False） | 409 |

### 评分接口验证

| # | 验证项 | 失败返回 |
|---|------|--------|
| 1 | Token 有效 | 401 |
| 2 | 当前用户是主管 | 403 |
| 3 | 是该员工的主管 | 403 |
| 4 | record_id 存在 | 404 |
| 5 | 员工已完成自评 | 409 |
| 6 | 未超过截止时间 | 422 |
| 7 | 分数 ∈ [1,5] | 422 |
| 8 | 指标值完整有效 | 422 |
| 9 | 未评分过（submit_locked=False） | 409 |

---

## 📝 请求响应示例

### 自评请求

```json
{
  "self_score": 4,
  "self_comment": "今年完成了销售目标的 95%，特别是...",
  "highlights": "新产品推进",
  "improvement_areas": "国际市场开拓"
}
```

### 自评响应

```json
{
  "record_id": "REC_001",
  "employee_id": "E001",
  "status": "reviewing",
  "self_score": 4,
  "self_comment": "...",
  "self_submitted_at": "2026-11-12T12:30:00",
  "self_review_deadline": "2026-11-20T23:59:59",
  "manager_review_deadline": "2026-11-25T23:59:59"
}
```

### 评分请求

```json
{
  "metric_values": [
    {"metric_code": "GMV", "value": 95, "unit": "万元", "comment": "超目标"},
    {"metric_code": "retention_rate", "value": 92, "unit": "%"}
  ],
  "manager_score": 4,
  "manager_comment": "完成良好，继续保持...",
  "rating_reason": "指标完成 90% 以上"
}
```

### 评分响应

```json
{
  "record_id": "REC_001",
  "employee_id": "E001",
  "status": "scored",
  "manager_score": 4,
  "manager_comment": "...",
  "manager_submitted_at": "2026-11-15T14:20:00",
  "metric_values_count": 2,
  "preliminary_score": 4.2
}
```

---

## 🔒 权限规则

### 自评接口

```
✅ 允许：
  - Token 有效的员工
  - 提交自己的自评

❌ 禁止：
  - 无 Token
  - Token 过期
  - HR 强制提交他人自评
  - 主管提交员工自评
  - 员工改他人自评
```

### 评分接口

```
✅ 允许：
  - 该员工的直接主管
  - HR（具有查看所有权限）

❌ 禁止：
  - 其他部门主管
  - 员工
  - 无相关权限的人
```

---

## 🚨 常见错误及对应 HTTP 状态

```python
# 1. 无效 Token
401 Unauthorized

# 2. 权限不足
403 Forbidden

# 3. Record 不存在
404 Not Found

# 4. 参数验证失败
422 Unprocessable Entity

# 5. 业务冲突（已提交/员工未自评）
409 Conflict

# 6. 服务器错误
500 Internal Server Error
```

---

## ⏱️ 截止时间控制

```python
# 自评截止时间
now() <= self_review_deadline  ✓

# 评分截止时间（通常比自评晚）
now() <= manager_review_deadline  ✓

# 超过截止时间
422 DEADLINE_EXCEEDED
```

---

## 💾 数据保存

### 自评提交时保存

- ✅ SelfReview 表：self_score, self_comment, highlights
- ✅ MetricValue 表：如果有指标值的话
- ✅ AssessmentRecord：status="reviewing", self_submitted_at
- ✅ 设置 submit_locked=True（防止重复提交）

### 评分提交时保存

- ✅ ManagerReview 表：manager_score, manager_comment
- ✅ MetricValue 表：所有指标值
- ✅ AssessmentRecord：status="scored", manager_submitted_at, confirmed_rating
- ✅ 设置 submit_locked=True（防止重复提交）

---

## 🧪 快速测试

```bash
# 1. 启动 API
python -m uvicorn backend.app.main:app --reload --port 8000

# 2. 员工自评
curl -X POST http://127.0.0.1:8000/v1/assessment/records/REC_001/self-review \
  -H "Authorization: Bearer emp_token_001" \
  -H "Content-Type: application/json" \
  -d '{"self_score": 4, "self_comment": "完成了大部分工作"}'

# 3. 主管评分
curl -X POST http://127.0.0.1:8000/v1/assessment/records/REC_001/manager-review \
  -H "Authorization: Bearer manager_token_001" \
  -H "Content-Type: application/json" \
  -d '{"metric_values": [{"metric_code": "GMV", "value": 95, "unit": "万元"}], "manager_score": 4, "manager_comment": "很好"}'
```

---

## 📚 完整文档

详细设计文档：`BLOCK3_DESIGN_SPECIFICATION.md`

包含：
- ✅ 完整的业务逻辑（8 步）
- ✅ 所有验证规则
- ✅ 数据库表设计
- ✅ 3 步验证计划
- ✅ 7 个 HTTP 集成测试用例

---

**概括**：两个接口（自评+评分）→ 权限隔离 → 状态管理 → 完成板块3 ✅

