# 🚀 快速启动和故障排除指南

## ✅ 系统状态

### 当前运行状态
- 🟢 **后端 (FastAPI)**: http://localhost:8000 - **运行中** ✓
- 🟢 **前端 (Vue 3)**: http://localhost:5180 - **运行中** ✓
- 🟢 **数据库**: SQLite (`data/perf.db`) - **正常** ✓

### 服务检查结果
```
✅ 后端 API 响应正常 (状态码: 200)
✅ 前端应用加载成功 (状态码: 200)
✅ 后端数据接口可用 (返回 92 个员工数据)
```

---

## 🌐 访问方式

### 方式 1: 直接访问应用
```
前端应用: http://localhost:5180
```

### 方式 2: 查看测试页面（验证连接）
```
测试页面: http://localhost:5180/test.html
```

### 方式 3: 通过 API 文档
```
API 文档: http://localhost:8000/docs
```

### 方式 4: 测试数据接口
```
员工列表: http://localhost:8000/v1/organizations/employees
部门列表: http://localhost:8000/v1/organizations/departments
```

---

## 🔐 登录凭证

> ⚠️ **口令不在源码里**（2026-11-24）⇒ 由环境变量 `TEST_USER_PASSWORDS` 注入，
> 且需 `PASSWORD_LOGIN_ENABLED=true` + `TEST_USERS_ENABLED=true`。
> 生产环境**默认全关**，只走飞书免登。

### 账号 1: 管理员 (HR)
```
账号: admin
角色: 系统管理员
权限: 发起考核、查看所有数据、确认成绩
```

### 账号 2: 总经理（管辖 10 个部门）
```
账号: yangfei
角色: 总经理（杨飞）
权限: 评分下属、查看下属数据
```

### 账号 3: 总经理（管辖 6 个部门）
```
账号: biyumi
角色: 总经理（别嘧咪）
权限: 评分下属、查看下属数据
```

### 账号 4: 员工
```
账号: employee
角色: 普通员工（李凯）
权限: 提交自评、查看个人成绩
```

> ⚠️ **账号 2 与 3 的角色权限相同**（都是总经理），
> 但**管辖范围不同**（10 个部门 vs 6 个）⇒ 「能不能看到某个人」这类用例可区分。

---

## 🔧 故障排除

### 问题 1: 前端页面空白

**可能原因**: 浏览器缓存、前端资源未加载

**解决方案**:
1. 清空浏览器缓存: `Ctrl+Shift+Delete`
2. 强制刷新: `Ctrl+Shift+R` 或 `Ctrl+F5`
3. 尝试隐身模式打开

---

### 问题 2: 无法连接到 localhost:5180

**可能原因**: 前端未启动、端口被占用

**检查步骤**:
```powershell
# 检查端口是否开放
netstat -ano | findstr "5180"

# 如果没有，重启前端
cd framework/frontend
npm run dev
```

---

### 问题 3: 无法连接到 localhost:8000

**可能原因**: 后端未启动、端口被占用

**检查步骤**:
```powershell
# 检查端口是否开放
netstat -ano | findstr "8000"

# 如果没有，重启后端
cd framework/backend
python -m uvicorn app.main:app --port 8000 --reload
```

---

### 问题 4: 登录失败

**可能原因**: 
- 后端未正确启动
- 数据库连接失败
- 前端和后端 API 代理配置错误

**解决方案**:
1. 检查后端日志是否有错误
2. 访问 http://localhost:8000/docs 验证 API
3. 查看浏览器控制台 (F12) 的 Network 标签检查请求

---

### 问题 5: 前后端无法通信

**可能原因**: 
- Vite 代理配置错误
- 防火墙阻止

**检查配置** (`frontend/vite.config.ts`):
```typescript
server: {
  proxy: {
    '/api': {
      target: 'http://localhost:8000',
      changeOrigin: true,
      rewrite: (path) => path.replace(/^\/api/, '/v1'),
    },
  },
}
```

**检查环境** (`frontend/.env`):
```
VITE_API_TARGET=http://localhost:8000
VITE_PORT=5180
```

---

## 🔄 完全重启步骤

如果以上都不行，执行完全重启：

### Step 1: 杀死现有进程
```powershell
# 杀死 Node 进程
Get-Process node | Stop-Process -Force

# 杀死 Python 进程
Get-Process python | Stop-Process -Force
```

### Step 2: 清理缓存
```bash
cd framework/frontend
rm -r node_modules dist .vite .nuxt
npm cache clean --force
```

### Step 3: 重新安装
```bash
npm install
```

### Step 4: 启动后端（终端 1）
```bash
cd framework/backend
python -m uvicorn app.main:app --port 8000 --reload
```

### Step 5: 启动前端（新开终端 2）
```bash
cd framework/frontend
npm run dev
```

### Step 6: 验证
访问 http://localhost:5180

---

## 📝 运行时可能出现的日志

### 正常的后端日志
```
INFO:     Uvicorn running on http://0.0.0.0:8000
INFO:     Application startup complete
[APP] Organizations router registered successfully
[APP] Performance router registered successfully
```

### 正常的前端日志
```
VITE v6.4.3  ready in 278 ms

  ➜  Local:   http://localhost:5180/
  ➜  Network: http://192.168.31.163:5180/
```

### 可以忽略的警告
```
npm warn Unknown env config "devdir".
Form data requires "python-multipart" to be installed.
```
这些不影响功能

---

## 🧪 快速测试

### 测试 1: 验证后端数据
在浏览器访问：
```
http://localhost:8000/v1/organizations/employees?limit=5
```
应该看到 JSON 格式的员工数据

### 测试 2: 验证前端连接
在浏览器访问：
```
http://localhost:5180/test.html
```
应该看到绿色的 ✅ 连接指示器

### 测试 3: 验证 API 文档
在浏览器访问：
```
http://localhost:8000/docs
```
应该看到 Swagger UI 的交互式 API 文档

---

## 🌍 网络访问

### 本地访问（推荐）
```
http://localhost:5180
```

### 局域网访问
```
http://192.168.31.163:5180
```

### 跨主机访问（VPN 或代理）
需要配置 API 目标地址

---

## 📚 更多文档

- **主文档**: `README.md` - 项目完整介绍
- **快速开始**: `QUICKSTART.md` - 30 分钟入门
- **API 文档**: `PERFORMANCE_API_SUMMARY.md` - 完整 API 说明
- **部署检查**: `DEPLOYMENT_CHECKLIST.md` - 部署验证清单
- **故障排除**: `TROUBLESHOOTING.md` - 常见问题解决

---

## 💬 常见问题

**Q: 如何修改 API 端口？**
```python
# backend/app/core/config.py
PORT = 8000  # 改这里
```

**Q: 如何关闭自动重载？**
```bash
python -m uvicorn app.main:app --port 8000  # 移除 --reload
```

**Q: 如何查看实时日志？**
```bash
# 终端会实时显示日志，或者查看：
tail -f framework/backend/app.log
```

**Q: 如何重置数据库？**
```bash
cd framework/backend
rm data/perf.db
python -m app.core.db  # 重新初始化
```

---

## ✅ 系统就绪检查清单

在认为系统完全就绪之前，确认：

- [ ] 访问 http://localhost:5180 能看到页面
- [ ] 访问 http://localhost:8000/docs 能看到 API 文档
- [ ] 能用 admin/admin123 登录
- [ ] 浏览器控制台没有红色错误
- [ ] 能查看员工列表和部门树
- [ ] 能完成一个考核流程（发起 → 自评 → 评分 → 确认）

---

## 🎉 祝你使用愉快！

**系统状态**: 🟢 **运行中**  
**版本**: 0.2.0  
**最后更新**: 2026-11-13 15:09  

有问题? 查看 TROUBLESHOOTING.md 或向技术支持团队联系。
