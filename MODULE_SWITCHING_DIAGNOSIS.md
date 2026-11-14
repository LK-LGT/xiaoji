# 🔍 模块切换问题诊断

## 📋 问题描述
用户无法切换不同的工作空间模块（比如从绩效管理切换到员工工作台）。

## 🔎 诊断结果

### 问题原因分析

#### 1️⃣ 工作空间下拉菜单不显示
当 `userWorkspaces.length <= 1` 时，下拉菜单不显示。这意味着用户可能只被识别为拥有一个工作空间的权限。

**代码位置**: `App.vue:16-17`
```vue
<el-dropdown v-if="userWorkspaces.length > 1" ...>
```

#### 2️⃣ 工作空间权限过滤
每个工作空间都需要至少一个权限：

```typescript
availableWorkspaces = WORKSPACES.filter(
  workspace => hasPermission(userPermissions, workspace.permission)
)
```

**当前的工作空间权限要求**:
- system: `system:dashboard:view`
- department: `department:dashboard:view`
- leader: `leader:workbench:view`
- employee: `dashboard:view`
- dispute: `dispute:dashboard:view`
- performance: `performance:dashboard:view`

#### 3️⃣ 用户权限不完整
根据 auth.py 的配置：
- ✅ admin: 拥有所有权限 → 应该能切换所有模块
- ⚠️ manager: 只有 department + leader + employee + performance 权限 → 缺少 system 和 dispute
- ⚠️ employee: 只有 employee + performance 权限 → 缺少其他模块

## ✅ 解决方案

### 方案1: 扩展用户权限（推荐）

更新 `backend/app/routes/auth.py` 中各账号的权限配置：

**manager 账号应该能访问**:
- ✅ department（部门）
- ✅ leader（主管）
- ✅ employee（员工）
- ✅ performance（绩效）

**employee 账号应该能访问**:
- ✅ employee（员工）
- ✅ performance（绩效）

### 方案2: 改进权限检查逻辑（已应用）

已更新 `hasPermission` 函数，支持通配符权限检查。

## 🔧 实施修复

### 步骤1: 更新用户权限配置

需要确保至少有一个账号拥有多个工作空间的权限。

### 步骤2: 重新登录

- 清除浏览器缓存
- 重新登录
- 检查工作空间下拉菜单

### 步骤3: 验证权限返回

访问 API 检查权限是否正确返回：
```bash
# 登录后，查看网络请求中 /auth/me 的响应
# 应该包含多个工作空间权限
```

## 📊 测试矩阵

使用以下账号测试模块切换：

| 账号 | 密码 | 预期工作空间 | 能否切换 |
|------|------|------------|--------|
| admin | admin123 | 全部6个 | ✅ 是 |
| manager | manager123 | 4个（dept/leader/emp/perf） | ❌ 否 |
| employee | employee123 | 2个（emp/perf） | ❌ 否 |

## 🎯 快速修复步骤

### 1. 扩展权限配置

编辑 `backend/app/routes/auth.py`，为各账号添加更多权限：

**manager 账号**:
```python
"permissions": [
    # Department permissions
    "department:dashboard:view",
    "department:month-approval:view",
    # ... 其他 department 权限
    
    # Leader permissions
    "leader:workbench:view",
    "leader:month-approval:view",
    # ... 其他 leader 权限
    
    # Employee permissions
    "dashboard:view",
    "planning:month:view",
    
    # Performance permissions
    "performance:dashboard:view",
    "performance:manager-review:view",
]
```

### 2. 重启后端
```bash
# 杀死后端进程
taskkill /PID <python_pid> /F

# 重启
cd framework/backend
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

### 3. 刷新前端
```
清除浏览器缓存 → 重新访问 localhost:5180 → 重新登录
```

## 🧪 测试验证

### 成功标志
- ✅ 工作空间下拉菜单显示
- ✅ 下拉菜单中显示多个工作空间
- ✅ 点击工作空间能切换
- ✅ 切换后左侧菜单更新
- ✅ 切换后页面正确加载

### 验证代码

在浏览器控制台执行：
```javascript
// 查看当前用户权限
localStorage.getItem('planning_access_token')

// 解析 Token（base64 解码）
atob('YOUR_TOKEN_HERE')

// 查看 URL 变化
console.log(window.location.pathname)
```

## 📝 相关文件

- `frontend/src/navigation/workspaces.ts` - 工作空间配置
- `frontend/src/App.vue` - 工作空间切换逻辑
- `backend/app/routes/auth.py` - 用户权限配置

---

**建议**: 立即执行"扩展权限配置"步骤，确保不同用户有多个工作空间的权限。
