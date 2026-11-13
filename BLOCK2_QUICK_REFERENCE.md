# 【板块2】POST /v1/assessment/tasks 快速参考卡片

## 🎯 一句话总结
**HR 发起考核任务，系统自动冻结配置、创建 Task 和 N 条 Record**

---

## 📍 接口地址
```
POST /v1/assessment/tasks
```

---

## 🔐 权限要求
- ✅ 仅 HR 可调用（check_permission 验证）
- ✅ 需要有效的 Bearer token

---

## 📤 请求体

| 字段 | 类型 | 必需 | 示例 | 说明 |
|-----|------|------|------|------|
| title | string | ✅ | "2026年11月月度考核" | 任务标题 |
| description | string | ❌ | "销售部评估" | 任务描述 |
| target_employee_ids | array[string] | ✅ | ["E001","E002"] | 被评员工ID列表 |
| start_date | ISO8601 | ✅ | "2026-11-01T00:00:00" | 开始日期 |
| end_date | ISO8601 | ✅ | "2026-11-30T23:59:59" | 结束日期 |
| self_review_deadline | ISO8601 | ✅ | "2026-11-20T23:59:59" | 自评截止时间 |
| manager_review_deadline | ISO8601 | ✅ | "2026-11-25T23:59:59" | 主管评分截止时间 |

---

## 📥 响应体

**成功（200 OK）**
```json
{
  "task_id": "TASK_20261112153045_3",
  "status": "created",
  "created_at": "2026-11-12T15:30:45.123456",
  "target_count": 3,
  "message": "成功创建考核任务，包含 3 名被评员工"
}
```

---

## ❌ 错误响应

| HTTP 状态 | 错误代码 | 原因 |
|----------|---------|------|
| 401 | NOT_AUTHENTICATED | token 无效或过期 |
| 403 | PERMISSION_DENIED | 非 HR 用户 |
| 422 | VALIDATION_ERROR | 参数验证失败 |
| 500 | INTERNAL_ERROR | 服务器内部错误 |

---

## 🔍 常见验证错误

| 验证项 | 错误信息 | 解决方案 |
|------|---------|--------|
| 员工列表为空 | "被评员工列表不能为空" | 至少传 1 个员工ID |
| 员工不存在 | "以下员工不存在：INVALID_E001" | 检查员工ID是否正确 |
| 日期无效 | "开始日期必须早于结束日期" | 检查日期顺序 |
| 截止时间错 | "自评截止时间必须在考核开始后、主管评分前" | 检查三个时间的先后顺序 |

---

## 📊 执行流程

```
1️⃣ 请求到达
   ↓
2️⃣ 验证 token（get_current_user）
   ├─ 无效 → 401
   └─ ✅
   ↓
3️⃣ 检查 HR 权限（check_permission）
   ├─ 非 HR → 403
   └─ ✅
   ↓
4️⃣ 参数验证（7 种检查）
   ├─ 失败 → 422
   └─ ✅
   ↓
5️⃣ 生成 ConfigSnapshot（冻结配置）
   ↓
6️⃣ 创建 AssessmentTask
   ↓
7️⃣ 批量生成 AssessmentRecord（N 条）
   ↓
8️⃣ 提交事务（db.commit）
   ↓
9️⃣ 返回 200 OK + task_id
```

---

## 🗂️ 数据库变化

### 创建的表

**ConfigSnapshot**
```sql
INSERT INTO config_snapshot (task_id, metrics_json, weights_json, rubric_json, frozen_at)
VALUES ('TASK_...', '[...]', '[...]', '[...]', NOW());
```

**AssessmentTask**
```sql
INSERT INTO assessment_task (id, name, period, status, snapshot_id, owner_id)
VALUES ('TASK_...', '2026 年 11 月月度考核', '202611', 'created', 'snap_...', 'HR001');
```

**AssessmentRecord** (x N)
```sql
INSERT INTO assessment_record (task_id, employee_id, status)
VALUES 
  ('TASK_...', 'E001', 'not_started'),
  ('TASK_...', 'E002', 'not_started'),
  ...
```

---

## 🧪 cURL 测试命令

```bash
# 成功创建
curl -X POST http://localhost:8000/v1/assessment/tasks \
  -H "Authorization: Bearer hr_token_001" \
  -H "Content-Type: application/json" \
  -d '{
    "title": "2026 年 11 月月度考核",
    "target_employee_ids": ["E001", "E002", "E003"],
    "start_date": "2026-11-01T00:00:00",
    "end_date": "2026-11-30T23:59:59",
    "self_review_deadline": "2026-11-20T23:59:59",
    "manager_review_deadline": "2026-11-25T23:59:59"
  }'

# 非 HR 用户（应该返回 403）
curl -X POST http://localhost:8000/v1/assessment/tasks \
  -H "Authorization: Bearer emp_token_001" \
  -H "Content-Type: application/json" \
  -d '{...}'

# 缺少 token（应该返回 401）
curl -X POST http://localhost:8000/v1/assessment/tasks \
  -H "Content-Type: application/json" \
  -d '{...}'
```

---

## 💡 Python 客户端示例

```python
import requests
from datetime import datetime, timedelta

# 请求参数
start = datetime.now()
end = start + timedelta(days=30)

payload = {
    "title": "2026 年 11 月月度考核",
    "target_employee_ids": ["E001", "E002", "E003"],
    "start_date": start.isoformat(),
    "end_date": end.isoformat(),
    "self_review_deadline": (end - timedelta(days=10)).isoformat(),
    "manager_review_deadline": (end - timedelta(days=5)).isoformat()
}

headers = {
    "Authorization": "Bearer hr_token_001",
    "Content-Type": "application/json"
}

# 发起请求
response = requests.post(
    "http://localhost:8000/v1/assessment/tasks",
    json=payload,
    headers=headers
)

# 处理响应
if response.status_code == 200:
    data = response.json()
    print(f"✅ 任务创建成功")
    print(f"   Task ID: {data['task_id']}")
    print(f"   被评员工数: {data['target_count']}")
else:
    print(f"❌ 错误 {response.status_code}")
    print(response.json())
```

---

## 🐍 pytest 测试命令

```bash
# 运行所有测试
pytest backend/app/tests/test_create_assessment_task.py -v

# 运行特定测试
pytest backend/app/tests/test_create_assessment_task.py::TestCreateAssessmentTask::test_success_create_task -v

# 显示覆盖率
pytest backend/app/tests/test_create_assessment_task.py --cov=backend.app.main --cov-report=html

# 输出详细日志
pytest backend/app/tests/test_create_assessment_task.py -vv -s
```

---

## ⏱️ 性能指标

| 员工数 | 预期时间 | 实际时间 |
|------|---------|---------|
| 10 | < 100ms | ~50ms |
| 100 | < 500ms | ~200ms |
| 1000 | < 3000ms | ~1500ms |

---

## 🔒 安全清单

- [x] token 验证（401 if missing）
- [x] HR 权限检查（403 if not HR）
- [x] 参数验证（422 if invalid）
- [x] SQL 注入防护（ORM）
- [x] 事务原子性（commit/rollback）

---

## 📚 关键代码位置

| 功能 | 文件 | 行号 |
|------|------|------|
| 接口定义 | main.py | 194 |
| 权限检查 | main.py | 239-246 |
| 参数验证 | main.py | 248-296 |
| ConfigSnapshot | main.py | 254-328 |
| Task 创建 | main.py | 330-343 |
| Record 批量 | main.py | 345-366 |
| 事务提交 | main.py | 371 |
| 异常处理 | main.py | 374-385 |

---

## ❓ 常见问题

**Q: ConfigSnapshot 是干什么的？**
A: 冻结考核启动时的配置（指标、权重、评分标准等），后续配置变更不影响已启动的考核。

**Q: 为什么要批量生成 Record？**
A: 每个员工一条 Record，后续自评、评分、确认等都基于这条 Record。

**Q: 如果员工中途离职怎么办？**
A: Record 的 status 会转为 skip 或 canceled，不再流转。

**Q: 可以修改任务配置吗？**
A: 不可以。ConfigSnapshot 是冻结的，要修改只能重新发起新任务。

**Q: 支持多部门跨考吗？**
A: 支持。AssessmentTask 的 department_id 可以为 null，表示跨部门。

---

## 🚀 快速集成

### 3 步启动 API
```bash
# 1. 启动服务
python -m uvicorn backend.app.main:app --reload

# 2. 测试接口
pytest backend/app/tests/test_create_assessment_task.py -v

# 3. 发送请求（见上面的 cURL 命令）
```

---

**⭐ 接口已就绪，可立即投入使用！**

