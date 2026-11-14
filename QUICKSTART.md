# 🎯 绩效管理系统 - 快速启动指南

## 📋 系统概述

这是一个完整的员工绩效管理系统，包括：
- ✅ **后端 API** (FastAPI + SQLAlchemy)
- ✅ **前端 UI** (Vue 3 + TypeScript + Element Plus)
- ✅ **组织结构管理** - 完整的部门树和员工体系
- ✅ **绩效考核流程** - 自评 → 主管评分 → 确认
- ✅ **权限控制系统** - 基于角色的细粒度访问控制

---

## 🚀 快速启动

### 方法 1: 自动启动脚本（推荐）

**启动后端:**
```bash
cd framework
python run-full-stack.py --backend
```

**启动前端:**（新打开一个终端）
```bash
cd framework
python run-full-stack.py --frontend
```

### 方法 2: 手动启动

**启动后端:**
```bash
cd framework/backend
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

**启动前端:**（新打开一个终端）
```bash
cd framework/frontend
npm install  # 首次运行需要安装依赖
npm run dev
```

---

## 📍 访问地址

| 服务 | 地址 | 用途 |
|------|------|------|
| **前端应用** | http://localhost:5180 | 用户界面 |
| **后端 API** | http://localhost:8000 | REST API |
| **API 文档** | http://localhost:8000/docs | Swagger 文档 |
| **OpenAPI 规范** | http://localhost:8000/openapi.json | API 规范 |

---

## 🔐 测试账号

### 登录凭证

| 账号 | 密码 | 角色 | 部门 |
|------|------|------|------|
| `admin` | `admin123` | 系统管理员 | HR 部 |
| `manager` | `manager123` | 部门主管 | 销售部 |
| `employee` | `employee123` | 普通员工 | 销售部 |

### 内部测试账号（用于 API 测试）

| 用户 ID | 姓名 | 角色 | 部门 |
|---------|------|------|------|
| `8d5c97gb` | 李夏冉 | HR | 人事行政部 |
| `2aaf32ea` | 李凯 | 工程师 | AI技术部 |
| `g426c684` | 杨飞 | 主管 | AI技术部 |

---

## 🔄 典型使用流程

### 1. HR 发起考核

```bash
curl -X POST 'http://localhost:8000/v1/performance/tasks?current_user_id=8d5c97gb' \
  -H 'Content-Type: application/json' \
  -d '{
    "title": "2026年11月月度考核",
    "period": "202611",
    "target_employee_ids": ["2aaf32ea", "17cd8645"],
    "start_date": "2026-10-14T00:00:00",
    "end_date": "2026-11-13T00:00:00",
    "self_review_deadline": "2026-11-15T00:00:00",
    "manager_review_deadline": "2026-11-18T00:00:00"
  }'
```

### 2. 员工提交自评

```bash
curl -X POST 'http://localhost:8000/v1/performance/self-reviews?current_user_id=2aaf32ea' \
  -H 'Content-Type: application/json' \
  -d '{
    "record_id": "REC_...",
    "performance_summary": "完成了核心开发任务",
    "achievements": ["实现RAG架构", "优化响应时间"],
    "challenges": ["硬件资源限制"]
  }'
```

### 3. 主管评分

```bash
curl -X POST 'http://localhost:8000/v1/performance/manager-reviews?current_user_id=g426c684' \
  -H 'Content-Type: application/json' \
  -d '{
    "record_id": "REC_...",
    "rating": "A",
    "manager_comment": "表现优秀，值得表扬"
  }'
```

### 4. 查询成绩

```bash
curl 'http://localhost:8000/v1/performance/my-performance?current_user_id=2aaf32ea'
```

---

## 📊 API 端点列表

### 组织结构 API

```
GET  /v1/organizations/departments/tree          # 完整组织树
GET  /v1/organizations/org-chart                 # 轻量级图表数据
GET  /v1/organizations/departments               # 分页查询部门
GET  /v1/organizations/departments/{id}          # 部门详情+员工
GET  /v1/organizations/employees                 # 分页查询员工
GET  /v1/organizations/employees/{id}            # 员工详情
```

### 绩效考核 API

```
POST /v1/performance/tasks                       # 发起考核任务 (HR)
GET  /v1/performance/tasks                       # 任务列表
GET  /v1/performance/tasks/{id}                  # 任务详情
GET  /v1/performance/tasks/{id}/records          # 任务记录列表
POST /v1/performance/tasks/{id}/confirm          # 确认最终成绩 (HR)
POST /v1/performance/self-reviews                # 提交自评 (员工)
GET  /v1/performance/records/{id}/self-review    # 查看自评
POST /v1/performance/manager-reviews             # 提交评分 (主管)
GET  /v1/performance/records/{id}                # 查看考核记录
GET  /v1/performance/my-performance              # 个人成绩查询
```

---

## 🗂️ 项目结构

```
framework/
├── backend/                    # 后端 (FastAPI)
│   ├── app/
│   │   ├── main.py            # 主应用入口
│   │   ├── domain/
│   │   │   └── models.py      # 数据模型
│   │   ├── routes/
│   │   │   ├── organizations.py    # 组织结构 API
│   │   │   ├── performance.py      # 绩效考核 API
│   │   │   ├── feishu.py          # 飞书同步
│   │   │   └── ...
│   │   ├── services/          # 业务逻辑
│   │   └── core/
│   │       ├── db.py          # 数据库配置
│   │       └── config.py      # 配置管理
│   ├── data/
│   │   └── perf.db            # SQLite 数据库
│   ├── requirements.txt        # Python 依赖
│   └── ...
│
├── frontend/                   # 前端 (Vue 3)
│   ├── src/
│   │   ├── views/             # 页面组件
│   │   ├── components/        # 可复用组件
│   │   ├── api/               # API 调用
│   │   ├── stores/            # Pinia 状态管理
│   │   ├── router/            # Vue Router 配置
│   │   └── main.ts            # 入口文件
│   ├── package.json           # npm 依赖
│   ├── vite.config.ts         # Vite 配置
│   └── ...
│
├── run-full-stack.py          # 启动脚本
└── QUICKSTART.md              # 本文件
```

---

## 🔧 环境配置

### 后端环境

**Python 版本**: 3.11+

**依赖安装:**
```bash
cd framework/backend
pip install -r requirements.txt
```

**数据库:**
- 默认使用 SQLite (`data/perf.db`)
- 支持 PostgreSQL（通过修改 `app/core/config.py`）

### 前端环境

**Node 版本**: 16.0+

**npm 版本**: 8.0+

**依赖安装:**
```bash
cd framework/frontend
npm install
```

---

## 🐛 常见问题

### Q1: 前端连接不到后端

**解决方法:**
1. 确保后端在 http://localhost:8000 运行
2. 检查 `frontend/.env` 中的 `VITE_API_TARGET=http://localhost:8000`
3. 检查浏览器的网络标签是否有 CORS 错误

### Q2: 数据库找不到

**解决方法:**
```bash
cd framework/backend
python -c "from app.core.db import init_db; init_db(); print('✓ 数据库初始化成功')"
```

### Q3: npm install 很慢

**解决方法:**
```bash
# 使用淘宝镜像
npm config set registry https://registry.npmmirror.com
npm install
```

### Q4: 权限错误 (403 Forbidden)

**解决方法:**
- 确保使用正确的 `current_user_id` 查询参数
- 检查用户角色是否有对应权限
- 参考上表中的测试账号

---

## 📈 监控和调试

### 后端日志

后端会在控制台输出详细的请求日志：
```
INFO:     Uvicorn running on http://0.0.0.0:8000
INFO:     Application startup complete
```

### 前端开发工具

Vue 3 提供了完整的调试工具。在浏览器安装 [Vue DevTools](https://devtools.vuejs.org/):
```
Chrome: https://chrome.google.com/webstore/...
Firefox: https://addons.mozilla.org/...
```

### API 文档

访问 http://localhost:8000/docs 查看交互式 API 文档

---

## 🧪 自动化测试

运行后端测试：
```bash
cd framework/backend
python -m pytest -xvs
```

运行前端测试：
```bash
cd framework/frontend
npm run test
```

---

## 📦 部署

### Docker 部署

**构建镜像:**
```bash
# 后端
cd framework/backend
docker build -t perf-backend .

# 前端
cd framework/frontend
docker build -t perf-frontend .
```

**运行容器:**
```bash
# 后端
docker run -p 8000:8000 perf-backend

# 前端
docker run -p 5180:5180 perf-frontend
```

### 云部署 (Vercel / Railway 等)

参考 `framework/frontend/Dockerfile` 和 `framework/backend/Dockerfile`

---

## 📞 技术支持

遇到问题？

1. 查看 `PERFORMANCE_API_SUMMARY.md` 获取详细 API 文档
2. 检查 http://localhost:8000/docs 的 Swagger 文档
3. 查看后端日志和浏览器控制台

---

**系统版本**: 0.2.0  
**最后更新**: 2026-11-13  
**状态**: 🟢 **生产就绪**
