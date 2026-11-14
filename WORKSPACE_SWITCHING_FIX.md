# 🔧 模块切换问题 - 完整修复方案

## ⚠️ 问题清单

用户在登录后无法通过工作空间下拉菜单切换不同的模块。

## 🎯 根本原因

### 原因1: 权限不足导致工作空间数量不足
- 工作空间下拉菜单需要 `userWorkspaces.length > 1`
- 某些账号可能只有一个工作空间的权限
- 结果: 即使有权访问多个模块，也无法切换

### 原因2: 工作空间识别问题
- 当用户点击菜单项后，路由改变
- 但工作空间可能识别不正确
- 结果: 左侧菜单没有更新

## ✅ 完整修复方案

### 第1步: 更新后端权限配置 ✅ (已完成)

**文件**: `backend/app/routes/auth.py`

已为 manager 账号扩展权限，现在包含：
- system:dashboard:view (系统管理 - 只读)
- department:* (部门权限)
- leader:* (主管权限)  
- employee:* (员工权限)
- performance:* (绩效管理权限)

employee 账号已包含：
- employee:* (员工权限)
- performance:* (绩效权限)

### 第2步: 优化前端权限检查 ✅ (已完成)

**文件**: `frontend/src/navigation/workspaces.ts`

已更新 `hasPermission` 函数，支持通配符权限。

### 第3步: 服务重启

```bash
# 关闭后端进程
taskkill /PID 3976 /F

# 重启后端
cd framework/backend
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

### 第4步: 前端重新加载

```
1. 清除浏览器缓存 (Ctrl+Shift+Del)
2. 强制刷新页面 (Ctrl+Shift+R)
3. 重新登录
```

## 🧪 验证方法

### 方法1: 检查工作空间下拉菜单

**登录 admin 账号后**:
```
预期: 左上角显示下拉菜单箭头 ▼
点击后: 显示6个工作空间选项
├─ 系统管理端
├─ 部门负责人端
├─ 直属领导端
├─ 员工工作台
├─ 裁决工作台
└─ 绩效管理
```

### 方法2: 检查权限数组长度

在浏览器开发者工具中执行:

```javascript
// 获取并解析 Token
const token = localStorage.getItem('planning_access_token')
const decoded = JSON.parse(atob(token))
console.log('Token:', decoded)

// 查看权限返回 (需要网络请求)
// 在 Network 标签查看 /auth/me 响应中的 permissions 数组长度
```

### 方法3: 检查可用工作空间

打开控制台，查看路由信息:
```javascript
console.log('Current Route:', window.location.pathname)
// 应该看到类似: /performance/dashboard 或 /employee/dashboard
```

## 📋 切换测试清单

### 使用 admin 账号测试

| 从 | 到 | 操作 | 预期结果 |
|---|----|------|--------|
| /performance | /employee | 点击下拉菜单选择"员工工作台" | ✓ 页面切换，左侧菜单更新 |
| /employee | /leader | 点击下拉菜单选择"直属领导端" | ✓ 页面切换，左侧菜单更新 |
| /leader | /system | 点击下拉菜单选择"系统管理端" | ✓ 页面切换，左侧菜单更新 |
| /system | /performance | 点击下拉菜单选择"绩效管理" | ✓ 页面切换，左侧菜单更新 |

### 使用 manager 账号测试

| 从 | 到 | 操作 | 预期结果 |
|---|----|------|--------|
| /performance | /leader | 点击下拉菜单选择"直属领导端" | ✓ 页面切换 |
| /leader | /department | 点击下拉菜单选择"部门负责人端" | ✓ 页面切换 |
| /department | /employee | 点击下拉菜单选择"员工工作台" | ✓ 页面切换 |

**注**: manager 无法访问 system（系统管理）和 dispute（裁决工作台）

### 使用 employee 账号测试

| 从 | 到 | 操作 | 预期结果 |
|---|----|------|--------|
| /performance | /employee | 点击下拉菜单选择"员工工作台" | ✓ 页面切换 |
| /employee | /performance | 点击下拉菜单选择"绩效管理" | ✓ 页面切换 |

**注**: employee 只能访问 employee 和 performance 两个模块

## 🎨 工作流程示例

### 场景: HR 审核员工的绩效

```
1. 以 admin 身份登录
   └─ 左上角显示: "系统管理端" ▼

2. 点击工作空间下拉菜单
   └─ 看到6个工作空间选项

3. 选择 "绩效管理"
   └─ URL 变为: /performance/dashboard
   └─ 左侧菜单显示: 管理工作台、员工自评、主管评分

4. 点击 "员工自评"
   └─ URL 变为: /performance/self-reviews
   └─ 页面加载自评管理界面

5. 点击工作空间下拉菜单，选择 "员工工作台"
   └─ URL 变为: /employee/dashboard
   └─ 左上角显示: "员工工作台" ▼
   └─ 左侧菜单更新为员工模块菜单
```

## 🐛 调试技巧

### 如果工作空间切换不起作用

**检查1**: 确认下拉菜单是否显示
```javascript
// 在控制台检查
console.log(document.querySelectorAll('.workspace-dropdown').length)
// 应该返回 1，表示下拉菜单存在
```

**检查2**: 查看权限是否正确返回
- 打开 DevTools → Network → XHR
- 登录时查看 POST /auth/login 响应
- 验证 permissions 数组包含多个权限

**检查3**: 验证路由切换
```javascript
// 在控制台监听路由变化
router.beforeEach((to, from) => {
  console.log(`从 ${from.path} 切换到 ${to.path}`)
})
```

### 如果菜单没有更新

可能是工作空间识别失败，检查:
```javascript
// 当前路径
console.log('Current path:', window.location.pathname)

// 应该匹配到某个工作空间
// 例如: /performance/dashboard 应该匹配 performance 工作空间
```

## 📁 修改清单

| 文件 | 修改 | 状态 |
|------|------|------|
| backend/auth.py | 扩展 manager 权限 | ✅ |
| frontend/workspaces.ts | 改进权限检查 | ✅ |
| 其他文件 | 无需修改 | ✅ |

## 🚀 立即执行步骤

### 第1步: 重启后端（重要）
```bash
# 杀死旧进程
taskkill /PID 3976 /F

# 启动新后端
cd D:\AI-Projects\performance-management\framework\backend
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

### 第2步: 刷新浏览器
```
1. 打开开发者工具 (F12)
2. 右键点击刷新按钮
3. 选择 "清空缓存并硬性重新加载"
```

### 第3步: 重新登录
```
用户名: admin
密码: admin123
```

### 第4步: 测试切换
```
1. 点击左上角工作空间下拉菜单
2. 选择不同的工作空间
3. 验证菜单更新
```

## ✨ 预期效果

修复完成后：
- ✅ 工作空间下拉菜单正常显示
- ✅ 可以切换到所有有权限的工作空间
- ✅ 切换时左侧菜单自动更新
- ✅ 页面内容正确加载
- ✅ 面包屑导航正确显示

---

**状态**: 🟢 **已修复，等待验证**

**下一步**: 按照上述步骤重启后端和刷新前端，然后测试工作空间切换功能。
