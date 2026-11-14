## ✅ 绩效考核管理系统 - 第二阶段完成

### 三大核心功能已上线

---

## 1️⃣ **员工&组织结构完善** - `/v1/organizations/*`

### 接口列表

| 端点 | 方法 | 用途 | 响应 |
|------|------|------|------|
| `/departments/tree` | GET | 完整组织树 | 嵌套树结构（含递归人数） |
| `/org-chart` | GET | 轻量级图表数据 | 平铺节点 + 父子关系 |
| `/departments` | GET | 分页查询部门 | 部门列表 |
| `/departments/{id}` | GET | 部门详情+直属员工 | 部门信息 + 员工列表 |
| `/employees` | GET | 分页查询员工 | 员工列表（可按部门过滤） |
| `/employees/{id}` | GET | 员工详情 | 个人信息 + 部门 |

### 数据质量

✓ **组织结构完整性**
```
一级部门: 10 个
二级及以下部门: 20 个
总员工数: 92 人
部门树完全递归: 支持无限层级
```

✓ **示例数据**
```
公司组织结构：
├─ 运营中心 (26 人)
│  ├─ 商务部 (5 人)
│  ├─ 直播部 (7 人)
│  ├─ 短视频 (5 人)
│  └─ 运营部 (9 人)
│
├─ 仓储部 (25 人)
│  ├─ 广州分部 (23 人)
│  └─ 湖北园区 (2 人)
│
├─ 商品中心 (17 人)
│  ├─ 买手部 (5 人)
│  └─ 版房 (12 人)
```

---

## 2️⃣ **绩效考核主流程** - `/v1/performance/*`

### 完整流程

#### 阶段1: 发起考核 (HR)
```
POST /v1/performance/tasks
  - 被评员工列表
  - 考核周期 (start_date, end_date)
  - 自评/主管评分截止时间

→ 创建 AssessmentTask + 批量 AssessmentRecord
```

**示例请求:**
```json
{
  "title": "2026 年 11 月月度考核",
  "period": "202611",
  "target_employee_ids": ["2aaf32ea", "17cd8645", "e76874b3"],
  "start_date": "2026-10-14T00:00:00",
  "end_date": "2026-11-13T00:00:00",
  "self_review_deadline": "2026-11-15T00:00:00",
  "manager_review_deadline": "2026-11-18T00:00:00"
}
```

**响应:**
```json
{
  "code": 0,
  "data": {
    "task_id": "TASK_20261113150121_202611",
    "status": "created",
    "target_count": 3,
    "message": "成功创建考核任务，包含 3 名被评员工"
  }
}
```

#### 阶段2: 员工自评
```
POST /v1/performance/self-reviews
  - record_id: 考核记录 ID
  - performance_summary: 业绩总结
  - achievements: 主要成就列表
  - challenges: 遇到的挑战列表

→ 记录状态: not_started → self_review_submitted
```

**权限**: 只能提交自己的自评

#### 阶段3: 主管评分
```
POST /v1/performance/manager-reviews
  - record_id: 考核记录 ID
  - rating: 等级 (A/B/C/D)
  - manager_comment: 主管评语

→ 记录状态: self_review_submitted → manager_review_submitted
```

**权限**: 主管只能评分下属

#### 阶段4: HR 确认
```
POST /v1/performance/tasks/{task_id}/confirm
  - 检查所有记录是否已评分
  - 更新任务状态为 confirmed

→ 记录状态: manager_review_submitted → confirmed
```

**权限**: 仅 HR 可操作

### 查询接口

```
GET /v1/performance/records/{record_id}
  → 查看单条考核记录（权限检查）

GET /v1/performance/tasks/{task_id}/records
  → 列出任务的所有记录（支持按状态/等级过滤）

GET /v1/performance/my-performance
  → 员工查询个人成绩列表
```

### 测试验证

✅ **完整流程测试通过**
```
[1] HR 发起考核任务 → 创建 3 条记录
    状态码: 200 ✓

[2] 员工提交自评 → 状态变更为 self_review_submitted
    状态码: 200 ✓

[3] 主管提交评分 → 状态变更为 manager_review_submitted，等级为 A
    状态码: 200 ✓

[4] 员工查看自己的考核记录 → 可查看完整信息（权限检查通过）
    状态码: 200 ✓
```

---

## 3️⃣ **权限验证体系** - Role-Based Access Control

### 权限规则

| 操作 | HR | 主管 | 员工 |
|------|----|----|------|
| 发起考核任务 | ✅ | ❌ | ❌ |
| 查看所有员工数据 | ✅ | 限自部门 | ❌ |
| 提交自评 | ❌ | ❌ | ✅（仅自己） |
| 提交评分 | ❌ | ✅（仅下属） | ❌ |
| 查看下属评分 | ❌ | ✅ | ❌ |
| 查看个人评分 | ✅（所有） | ✅（下属） | ✅（自己） |
| 确认最终成绩 | ✅ | ❌ | ❌ |

### 实现方式

**1. HR 识别**
```python
def check_is_hr(db, employee_id):
    # 检查部门是否为 HR 相关部门
    emp = db.query(Employee).filter(Employee.id == employee_id).first()
    return emp.department_id in ("DEPT_HR", "cc7a7gf9e937gb42")
```

**2. 主管关系验证**
```python
def check_is_manager_of(db, manager_id, employee_id):
    # 检查 manager_id 是否为 employee_id 的直属主管
    emp = db.query(Employee).filter(Employee.id == employee_id).first()
    return emp.manager_id == manager_id
```

**3. 数据可见性**
```python
def check_can_view_employee(db, viewer_id, target_employee_id):
    if check_is_hr(db, viewer_id):
        return True  # HR 可看所有
    if viewer_id == target_employee_id:
        return True  # 本人可看自己
    if check_is_manager_of(db, viewer_id, target_employee_id):
        return True  # 主管可看下属
    return False
```

### 测试场景

✅ **权限控制通过**
```
场景1: 员工尝试发起考核任务
  → 拒绝: 403 Forbidden ✓

场景2: 员工提交自己的自评
  → 允许: 200 OK ✓

场景3: HR 查看所有员工
  → 允许: 200 OK ✓

场景4: 主管查看下属评分
  → 允许: 200 OK ✓

场景5: 主管查看非下属评分
  → 拒绝: 403 Forbidden ✓
```

---

## 📊 数据库架构

### 新增/修改的表

**assessment_record** - 考核记录
```sql
id                   VARCHAR(64) PRIMARY KEY
task_id              VARCHAR(64) FOREIGN KEY → assessment_task
employee_id          VARCHAR(64) FOREIGN KEY → employee
status               VARCHAR(32)  -- not_started / self_review_submitted / manager_review_submitted / confirmed
self_review_content  JSON         -- 自评内容 (NEW)
manager_comment      TEXT         -- 主管评语 (NEW)
confirmed_rating     VARCHAR(8)   -- 最终等级 (A/B/C/D)
created_at           DATETIME
updated_at           DATETIME
```

**assessment_task** - 考核任务
```sql
id                VARCHAR(64) PRIMARY KEY
name              VARCHAR(128)  -- 任务标题
period            VARCHAR(16)   -- 考核期间 (202611)
status            VARCHAR(32)   -- created / confirmed
owner_id          VARCHAR(64)   -- HR 创建人
created_at        DATETIME
```

---

## 🔄 用户流程示例

### 典型的考核周期

**周一: HR 发起考核**
```bash
curl -X POST http://localhost:8000/v1/performance/tasks \
  ?current_user_id=8d5c97gb \
  -H "Content-Type: application/json" \
  -d '{
    "title": "11 月月度考核",
    "period": "202611",
    "target_employee_ids": ["2aaf32ea", "17cd8645"],
    ...
  }'
```

**周二-周三: 员工自评**
```bash
curl -X POST http://localhost:8000/v1/performance/self-reviews \
  ?current_user_id=2aaf32ea \
  -d '{
    "record_id": "REC_...",
    "performance_summary": "...",
    ...
  }'
```

**周四: 主管评分**
```bash
curl -X POST http://localhost:8000/v1/performance/manager-reviews \
  ?current_user_id=g426c684 \
  -d '{
    "record_id": "REC_...",
    "rating": "A",
    "manager_comment": "..."
  }'
```

**周五: HR 确认**
```bash
curl -X POST http://localhost:8000/v1/performance/tasks/{task_id}/confirm \
  ?current_user_id=8d5c97gb
```

**周末: 员工查询成绩**
```bash
curl -X GET http://localhost:8000/v1/performance/my-performance \
  ?current_user_id=2aaf32ea
```

---

## 🚀 下一步计划

### 已完成 ✅
- [x] 1. 员工&组织结构完善 API
- [x] 2. 绩效考核主流程 API
- [x] 3. 权限验证体系

### 待进行 ⏳
- [ ] 指标数据导入与管理
- [ ] 自动计分引擎
- [ ] AI 评语生成
- [ ] 前端 UI 建设
- [ ] 报表与数据分析
- [ ] 权限细节化（部门维度）

---

## 📞 API 清单

### 组织结构 API
- `GET /v1/organizations/departments/tree`
- `GET /v1/organizations/org-chart`
- `GET /v1/organizations/departments`
- `GET /v1/organizations/departments/{id}`
- `GET /v1/organizations/employees`
- `GET /v1/organizations/employees/{id}`

### 绩效考核 API
- `POST /v1/performance/tasks` (发起考核)
- `GET /v1/performance/tasks` (查询任务列表)
- `GET /v1/performance/tasks/{id}` (任务详情)
- `GET /v1/performance/tasks/{id}/records` (任务记录列表)
- `POST /v1/performance/tasks/{id}/confirm` (确认成绩)
- `POST /v1/performance/self-reviews` (提交自评)
- `GET /v1/performance/records/{id}/self-review` (查看自评)
- `POST /v1/performance/manager-reviews` (提交评分)
- `GET /v1/performance/records/{id}` (查看考核记录)
- `GET /v1/performance/my-performance` (个人成绩查询)

---

## 🎯 验证检查清单

- [x] 组织树完整递归
- [x] 部门人数统计正确
- [x] 考核任务创建成功
- [x] 自评状态流转正确
- [x] 主管评分权限检查
- [x] 员工数据可见性控制
- [x] 数据库表结构完整
- [x] API 接口可用

---

**系统状态**: 🟢 **可用**  
**最后更新**: 2026-11-13 15:01:21 UTC  
**版本**: 0.2.0
