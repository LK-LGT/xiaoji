# 🔧 前端无法进入 - 故障排除

> ⚠️ **端口说明（2026-11-26）**
> **本机后端跑在 8001**（8000 被另一个项目「买手流程智能中控台」占用）。
> 下文凡是 `8000` 的命令，**本机一律改成 `8001`**；
> 部署环境仍用 8000（项目默认）。

## 可能的问题

### 问题 1: 浏览器缓存
**症状**: 页面空白或显示老版本

**解决方案:**
```
按 Ctrl+Shift+Delete 清空浏览器缓存
或者 Ctrl+Shift+R 强制刷新页面
```

### 问题 2: 前端依赖未完全加载
**症状**: 页面加载缓慢或报错 404

**解决方案:**
```bash
cd framework/frontend
# 清空缓存
rm -r node_modules package-lock.json
# 重新安装
npm install
# 重新启动
npm run dev
```

### 问题 3: 后端 API 连接问题
**症状**: 登录失败或无法加载数据

**检查方法:**
```bash
# 测试后端 API
curl http://localhost:8001/v1/organizations/employees?limit=1

# 测试 API 文档
访问 http://localhost:8001/docs
```

**如果 API 无响应:**
```bash
# 重启后端
cd framework/backend
python -m uvicorn app.main:app --port 8001 --reload
```

### 问题 4: 前端配置错误
**症状**: 页面加载但无法请求数据

**检查前端配置:**
```bash
# 编辑 frontend/.env
VITE_API_TARGET=http://localhost:8001  # 确保这一行正确（本机端口）
VITE_PORT=5180
```

**编辑 frontend/vite.config.ts:**
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

### 问题 5: 防火墙阻止
**症状**: 无法连接到 localhost

**解决方案:**
```bash
# Windows 防火墙允许 Python 和 Node
1. 打开 Windows 防火墙
2. 允许 python.exe 和 node.exe
3. 允许端口 8000 和 5180
```

---

## 快速诊断步骤

### Step 1: 检查后端
```bash
Invoke-WebRequest -Uri http://localhost:8000/docs
# 应该返回 200 OK
```

### Step 2: 检查前端页面
```bash
Invoke-WebRequest -Uri http://localhost:5180
# 应该返回 200 OK
```

### Step 3: 检查后端数据
```bash
Invoke-WebRequest -Uri http://localhost:8000/v1/organizations/employees?limit=1
# 应该返回 JSON 数据
```

### Step 4: 检查前端日志
打开浏览器的开发者工具 (F12) 检查 Console 和 Network 标签

---

## 完全重启步骤

如果以上都不行，完全重启系统：

### Step 1: 杀死现有进程
```powershell
# 杀死后端
Get-Process python | Where-Object {$_.Path -like "*uvicorn*"} | Stop-Process -Force

# 杀死前端
Get-Process node | Stop-Process -Force
```

### Step 2: 清空缓存
```bash
cd framework/frontend
rm -r node_modules dist .nuxt .vite
npm cache clean --force
```

### Step 3: 重新安装依赖
```bash
cd framework/frontend
npm install
```

### Step 4: 重新启动
```bash
# 终端 1 - 后端
cd framework/backend
python -m uvicorn app.main:app --port 8000 --reload

# 终端 2 - 前端  
cd framework/frontend
npm run dev
```

### Step 5: 访问
- 前端: http://localhost:5180
- API: http://localhost:8000/docs

---

## 测试账号

| 账号 | 身份 |
|------|------|
| admin | 林雅静（系统管理员）|
| yangfei | 杨飞（总经理）|
| biyumi | 别嘧咪（总经理）|
| employee | 李凯（普通员工）|

> ⚠️ **口令不在源码里** ⇒ 需设环境变量 `TEST_USER_PASSWORDS`，
> 并开启 `PASSWORD_LOGIN_ENABLED=true` + `TEST_USERS_ENABLED=true`。
> 未开启时 `/v1/auth/login` 返回 **503** `PASSWORD_LOGIN_DISABLED`
> ⇒ 这是**预期行为**，不是故障 ⇒ 生产只走飞书免登。

---

## 常见错误信息

### "Cannot GET /"
**原因**: 前端页面加载失败  
**解决**: 清缓存、重新安装依赖

### "API Error 404"
**原因**: 后端 API 不可用  
**解决**: 检查后端是否运行、检查 vite 代理配置

### "CORS Error"
**原因**: 跨域请求被阻止  
**解决**: 确保后端运行在 localhost:8000，前端在 localhost:5180

### "Connection refused"
**原因**: 无法连接到服务  
**解决**: 检查端口是否被占用，检查防火墙

---

## 验证系统正常

当你看到以下内容时，系统 ✅ 完全正常：

1. **后端**
   - ✅ "Application startup complete"
   - ✅ 访问 http://localhost:8000/docs 看到 Swagger UI

2. **前端**
   - ✅ "ready in XXX ms"
   - ✅ 访问 http://localhost:5180 看到登录界面

3. **连接**
   - ✅ 浏览器登录成功
   - ✅ 可以查看员工列表、部门树等数据

---

## 需要帮助？

尝试上述步骤后如果还是无法进入，请提供：
1. 浏览器控制台的错误信息
2. 后端的完整日志输出
3. 前端的 npm 输出
