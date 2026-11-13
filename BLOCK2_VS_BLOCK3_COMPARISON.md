# 【板块2 vs 板块3】对比分析

**目的**：理解两个板块的关系和差异

---

## 📊 业务流程对比

### 【板块2】创建考核任务

**谁操作**：HR 部门  
**操作内容**：定义一个考核任务  
**产出物**：n 条 AssessmentRecord（每个员工一条）  
**状态**：所有 Record 初始状态为 `not_started`

```
HR 视角：
  创建一次考核任务 → 生成 100 条员工评估记录 → 分发给主管和员工

技术视角：
  POST /v1/assessment/tasks
  ├─ 创建 1 个 Task
  ├─ 冻结 1 个 ConfigSnapshot
  └─ 生成 n 个 Records
```

---

### 【板块3】员工自评和主管评分

**谁操作**：员工 + 主管  
**操作内容**：员工填写自评，主管填写评分  
**产出物**：SelfReview 表 + ManagerReview 表 + MetricValue 表  
**状态转移**：`not_started` → `reviewing` → `scored`

```
员工视角：
  看到待评估 → 填写自评 → 提交 ✓

主管视角：
  看到待评分员工 → 填入指标值和评分 → 提交 ✓

技术视角：
  POST /v1/assessment/records/{id}/self-review
    └─ 保存 SelfReview + 更新 status
  
  POST /v1/assessment/records/{id}/manager-review
    └─ 保存 ManagerReview + MetricValue + 更新 status
```

---

## 🔄 数据流对比

### 【板块2】核心数据

| 表名 | 用途 | 记录数 |
|-----|------|-------|
| AssessmentTask | 考核任务 | 1 |
| ConfigSnapshot | 冻结配置 | 1 |
| AssessmentRecord | 评估记录 | n（员工数） |

### 【板块3】新增数据

| 表名 | 用途 | 记录数 |
|-----|-----|-------|
| SelfReview | 员工自评 | n |
| ManagerReview | 主管评分 | n |
| MetricValue | 指标值 | n × m（指标数） |

---

## 👥 参与者对比

### 【板块2】参与者

| 角色 | 操作 | 权限 |
|-----|------|------|
| HR | 创建任务 | 全局 |
| 主管 | 查看自己的待评员工 | 部门级 |
| 员工 | 被动接收 | 无 |

### 【板块3】参与者

| 角色 | 操作 | 权限 |
|-----|------|------|
| 员工 | 自评 | 仅自己 |
| 主管 | 评分 | 部门级（下属） |
| HR | 查看/管理 | 全局 |

---

## ⏱️ 时间流对比

### 【板块2】一次性

```
时刻 T0：
  ├─ HR 创建考核任务
  ├─ 系统生成 n 条 Record
  └─ 状态：all Record.status = "not_started"
```

### 【板块3】分阶段

```
时刻 T1（自评期）：
  ├─ 员工提交自评
  ├─ Record.status = "reviewing"
  └─ 截止时间：deadline_1

时刻 T2（评分期）：
  ├─ 主管提交评分
  ├─ Record.status = "scored"
  └─ 截止时间：deadline_2（> deadline_1）

时刻 T3（计分期）：
  ├─ 规则引擎计算最终分数
  └─ 进入【板块4】
```

---

## 🔐 权限对比

### 【板块2】权限模型

```
POST /v1/assessment/tasks
  ├─ HR 角色 ✅
  ├─ Token 有效 ✅
  └─ 拒绝所有非 HR ❌
```

**权限级别**：1（全局 HR）

### 【板块3】权限模型

```
POST /v1/assessment/records/{id}/self-review
  ├─ Token 有效 ✅
  ├─ 当前用户 == 被评人 ✅
  └─ 拒绝其他用户 ❌

POST /v1/assessment/records/{id}/manager-review
  ├─ Token 有效 ✅
  ├─ 当前用户的 role = "主管" ✅
  ├─ 当前用户 == 被评人的主管 ✅
  └─ 拒绝其他用户 ❌
```

**权限级别**：2（身份 + 关系）

---

## ✅ 验证复杂度对比

### 【板块2】验证层数

```
第 1 层：Token 有效性
第 2 层：角色检查（HR）
第 3 层：参数验证（7 个参数）
第 4 层：参数逻辑（日期顺序）
第 5 层：员工存在性
第 6 层：部门存在性
第 7 层：配置有效性
```

**共计**：7 层验证

### 【板块3】验证层数

#### 自评接口

```
第 1 层：Token 有效性
第 2 层：角色检查（员工）
第 3 层：身份关系（自己的评估）
第 4 层：参数验证（分数、评论）
第 5 层：参数逻辑（评论长度）
第 6 层：时间检查（截止时间）
第 7 层：状态检查（not_started）
第 8 层：重复提交检查（submit_locked）
```

**共计**：8 层验证

#### 评分接口

```
第 1 层：Token 有效性
第 2 层：角色检查（主管）
第 3 层：身份关系（是下属）
第 4 层：参数验证（分数、指标值）
第 5 层：指标有效性（代码、范围）
第 6 层：时间检查（截止时间）
第 7 层：状态检查（reviewing）
第 8 层：前置条件（员工已自评）
第 9 层：重复提交检查（submit_locked）
```

**共计**：9 层验证

---

## 🧪 测试数量对比

### 【板块2】测试用例

```
第一步（代码自查）：11 个检查点
第二步（单元测试）：2 PASS + 5+ 核心逻辑验证
第三步（HTTP 集成）：7 个场景
```

**总计**：~20 个测试场景

### 【板块3】预计测试用例

```
自评接口测试：
  ├─ 参数验证（4）：empty_comment, invalid_score, expired, etc.
  ├─ 权限测试（2）：non_employee, wrong_person
  ├─ 业务逻辑（3）：success, state_transition, resubmit_rejection
  └─ 异常处理（3）：deadline, already_submitted, etc.

评分接口测试：
  ├─ 参数验证（5）：empty_metrics, invalid_value, etc.
  ├─ 权限测试（2）：non_manager, non_direct_manager
  ├─ 业务逻辑（3）：success, employee_not_reviewed, state_check
  └─ 异常处理（3）：deadline, already_reviewed, etc.

HTTP 集成测试：
  ├─ 成功场景（2）：self_review_success, manager_review_success
  ├─ 权限拒绝（3）：non_emp, wrong_manager, etc.
  ├─ 参数错误（5）：invalid_score, metrics_missing, etc.
  └─ 业务冲突（2）：already_submitted, employee_not_reviewed
```

**总计**：~35 个测试场景

---

## 📈 代码复杂度对比

### 【板块2】代码规模

```
routes/v1/assessment.py
  ├─ create_assessment_task 函数
  │   ├─ 权限检查（5 行）
  │   ├─ 参数验证（15 行）
  │   ├─ 业务逻辑（30 行）
  │   └─ 异常处理（10 行）
  └─ 总计：~60 行核心逻辑
```

### 【板块3】代码规模

```
routes/v1/assessment_records.py
  ├─ submit_self_review 函数（~80 行）
  │   ├─ 权限检查
  │   ├─ 参数验证
  │   ├─ 时间检查
  │   ├─ 状态检查
  │   ├─ 业务逻辑
  │   └─ 异常处理
  │
  └─ submit_manager_review 函数（~100 行）
      ├─ 权限检查（更复杂）
      ├─ 参数验证
      ├─ 指标值验证（新增）
      ├─ 时间检查
      ├─ 状态检查
      ├─ 业务逻辑
      └─ 异常处理
```

**总计**：~180 行核心逻辑（约 3 倍）

---

## 🎯 技术难点对比

### 【板块2】技术难点

```
1. ✅ 权限隔离（HR 检查）
2. ✅ 配置冻结（JSON 序列化）
3. ✅ 批量操作（100+ 员工）
4. ✅ 事务管理（原子性）
```

**难度**：⭐⭐

### 【板块3】技术难点

```
1. ✅ 权限隔离（双重检查：身份 + 关系）
2. ✅ 状态转移（多步骤状态机）
3. ✅ 时间控制（截止时间检查）
4. ✅ 重复提交保护（submit_locked）
5. ⭐ 指标值验证（ConfigSnapshot 中的指标）
6. ⭐ 前置条件检查（员工必须先自评）
```

**难度**：⭐⭐⭐（复杂 50%）

---

## 🚀 实现优先级对比

### 【板块2】优先级

```
必需 ✅ - 最基础的业务
└─ 没有板块2，无法开始后续工作
```

### 【板块3】优先级

```
必需 ✅ - 自评和评分是核心流程
├─ 自评接口优先级：最高（给员工用）
├─ 评分接口优先级：很高（给主管用）
└─ 完成后才能进入板块4
```

---

## 📝 总结表

| 维度 | 【板块2】 | 【板块3】 | 差异 |
|------|---------|---------|------|
| 参与者 | 1（HR） | 3（HR+主管+员工） | +2 |
| 权限模型 | 1 层 | 2 层 | +100% |
| 验证层数 | 7 | 8-9 | +20% |
| 代码行数 | ~60 | ~180 | +200% |
| 数据表 | 3 | 6（+3） | +100% |
| 测试用例 | ~20 | ~35 | +75% |
| 技术难度 | ⭐⭐ | ⭐⭐⭐ | +50% |
| 工作量 | 3h | 6h | +100% |

---

**结论**：【板块3】是【板块2】的自然延伸，复杂度和工作量都翻倍，但都是必需的业务功能。

