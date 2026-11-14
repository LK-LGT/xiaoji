# 📅 中期任务规划 (本周内)

## 🎯 目标
实现关键页面的实际功能与后端 API 集成

## 📊 任务分解

### 优先级 1️⃣: 部门/员工管理 (最高)

#### 任务 1.1: 部门管理 CRUD 完整化
**文件**: `backend/app/routes/v1/system.py`

需要实现:
```python
# 1. getDepartments() - 从数据库加载真实部门树
# 2. createDepartment() - 保存到数据库
# 3. updateDepartment() - 更新部门信息
# 4. deleteDepartment() - 删除部门 (需要验证是否为空)
```

**预计工作量**: 2-3 小时  
**复杂度**: 中等

---

#### 任务 1.2: 员工管理 CRUD
**文件**: `backend/app/routes/v1/system.py` + `backend/app/models/`

需要实现:
```python
# 1. getEmployees() - 分页查询员工
# 2. getEmployee(id) - 获取单个员工详情
# 3. createEmployee() - 新增员工
# 4. updateEmployee() - 修改员工信息
# 5. deleteEmployee() - 删除员工
```

**数据字段**:
- id, username, realName, email, phone
- dept_id, position, status
- hire_date, leave_date

**预计工作量**: 3-4 小时  
**复杂度**: 中等

---

### 优先级 2️⃣: 角色权限管理

#### 任务 2.1: 角色管理 CRUD
**文件**: `backend/app/routes/v1/system.py`

需要实现:
```python
# 1. getRoles() - 列表查询
# 2. createRole() - 新增角色
# 3. updateRole() - 修改角色
# 4. deleteRole() - 删除角色
```

**预计工作量**: 2-3 小时  
**复杂度**: 简单

---

#### 任务 2.2: 权限管理
**文件**: `backend/app/routes/v1/system.py`

需要实现:
```python
# 1. getPermissions() - 权限树
# 2. rolePermissions(roleId) - 角色权限
# 3. assignPermissions(roleId, permissionIds) - 分配权限
```

**预计工作量**: 2-3 小时  
**复杂度**: 中等

---

### 优先级 3️⃣: 绩效管理功能

#### 任务 3.1: 员工自评表单
**文件**: `frontend/src/views/performance/EmployeeSelfReview.vue`  
**后端**: `backend/app/routes/performance.py`

需要实现:
```python
# 1. getSelfReviews(userId) - 查询自评记录
# 2. saveSelfReview() - 保存自评
# 3. submitSelfReview() - 提交自评
```

**前端需求**:
- 显示员工信息
- 自评表单（多字段）
- 提交/保存按钮
- 验证规则

**预计工作量**: 4-5 小时  
**复杂度**: 高

---

#### 任务 3.2: 主管评分
**文件**: `frontend/src/views/performance/ManagerReview.vue`  
**后端**: `backend/app/routes/performance.py`

需要实现:
```python
# 1. getReviewTasks() - 获取评分任务列表
# 2. saveReview() - 保存评分
# 3. submitReview() - 提交评分
```

**预计工作量**: 4-5 小时  
**复杂度**: 高

---

## 📋 执行计划

### 第1天: 数据库集成
- [ ] 检查/完善数据库模式 (models)
- [ ] 为部门创建数据库操作函数
- [ ] 为员工创建数据库操作函数
- **时间**: 4 小时
- **验收标准**: 能从数据库查询部门和员工列表

### 第2天: 员工和部门管理
- [ ] 实现部门 CRUD
- [ ] 实现员工 CRUD
- [ ] 前端集成测试
- **时间**: 6 小时
- **验收标准**: 可在 UI 上新增/编辑/删除部门和员工

### 第3天: 角色权限管理
- [ ] 实现角色 CRUD
- [ ] 实现权限分配
- [ ] 前端测试
- **时间**: 5 小时
- **验收标准**: 可管理角色和权限

### 第4-5天: 绩效管理
- [ ] 实现自评功能
- [ ] 实现评分功能
- [ ] 完整的端到端测试
- **时间**: 8 小时
- **验收标准**: 完整的自评和评分流程可用

---

## 🏗️ 技术方案

### 后端 API 设计模式

```python
# 1. 统一的响应格式
{
  "code": 0,              # 0=成功, 其他=错误码
  "message": "操作成功",
  "data": {...}           # 实际数据
}

# 2. 错误响应
{
  "code": 1,
  "message": "错误描述",
  "errors": [...]
}

# 3. 分页响应
{
  "code": 0,
  "data": {
    "records": [...],
    "total": 100,
    "pageNo": 1,
    "pageSize": 20
  }
}
```

### 前端 API 调用模式

```typescript
// 1. 加载数据
async function load() {
  loading.value = true
  try {
    data.value = await api.getDepartments()
  } catch (error) {
    ElMessage.error(error.message)
  } finally {
    loading.value = false
  }
}

// 2. 保存数据
async function save() {
  if (!await formRef.validate()) return
  saving.value = true
  try {
    await api.createDepartment(form.value)
    ElMessage.success('保存成功')
    await load()
  } catch (error) {
    ElMessage.error(error.message)
  } finally {
    saving.value = false
  }
}
```

---

## 🧪 测试计划

### 单元测试
```python
# backend/tests/test_system.py
def test_get_departments():
    """测试获取部门列表"""
    
def test_create_department():
    """测试创建部门"""
    
def test_update_department():
    """测试更新部门"""
    
def test_delete_department():
    """测试删除部门"""
```

### 集成测试
```typescript
// frontend/tests/views/system/OrgManagement.spec.ts
describe('OrgManagement', () => {
  it('should load departments on mount')
  it('should create new department')
  it('should update department')
  it('should delete department')
})
```

### 手动测试 (UI)
| 功能 | 操作步骤 | 预期结果 |
|------|--------|--------|
| 加载部门列表 | 打开页面 | 显示部门树 |
| 新增部门 | 点击新增按钮 | 打开表单，填写后保存 |
| 编辑部门 | 选择部门，点击编辑 | 表单弹出，修改并保存 |
| 删除部门 | 选择部门，点击删除 | 确认后删除 |

---

## 📊 进度追踪

### 完成度指标
- [ ] 30% - 数据库集成完成
- [ ] 60% - 员工部门管理完成
- [ ] 80% - 角色权限管理完成
- [ ] 100% - 绩效管理完成

### 风险评估
| 风险 | 影响 | 缓解措施 |
|------|------|--------|
| 数据库表结构不完整 | 高 | 提前检查和完善模式 |
| 前后端接口不匹配 | 中 | 严格遵循 API 文档 |
| 性能问题 | 中 | 实现分页和缓存 |

---

## 📚 参考资源

### 关键文件
- API 文档: `frontend/src/api/system.ts`
- 前端页面: `frontend/src/views/system/`
- 后端路由: `backend/app/routes/v1/system.py`
- 数据模型: `backend/app/models/`

### 相关工具
- API 测试: Postman / Thunder Client / Bruno
- 数据库工具: DBeaver / MySQL Workbench
- 前端调试: Vue DevTools

---

## ✅ 完成清单

### 代码检查点
- [ ] 后端代码通过 linting
- [ ] 前端代码通过 linting
- [ ] 所有导入正确
- [ ] 没有 TypeScript 错误
- [ ] 没有控制台 console.error

### 功能检查点
- [ ] 基本 CRUD 操作完整
- [ ] 错误处理完善
- [ ] 验证规则完整
- [ ] 用户反馈清晰
- [ ] 性能可接受

### 文档检查点
- [ ] API 文档更新
- [ ] 代码注释完整
- [ ] README 更新
- [ ] 部署指南完整

---

## 🚀 下周目标 (长期)

- 完整的用户权限控制
- 数据审计和日志
- 前端路由权限保护
- API 文档自动生成
- 单元测试覆盖
- 集成测试覆盖
- 性能优化
- 部署流程自动化

---

**开始日期**: 2026-11-14  
**预计完成**: 2026-11-21  
**总工时**: 25-30 小时

