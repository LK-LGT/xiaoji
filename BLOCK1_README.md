# 【板块1：鉴权权限层】实现指南

## 📚 文档导航

### 🎯 快速开始（5 分钟）

1. **快速验证** → `BLOCK1_QUICK_TEST.py`
   - 验证所有函数是否正常工作
   - 无需 FastAPI 应用，可独立运行
   - 命令：`python BLOCK1_QUICK_TEST.py`

2. **快速参考** → `BLOCK1_QUICK_REFERENCE.md`
   - 核心函数速查表
   - Mock Token 列表
   - 权限矩阵速览
   - FastAPI 路由模板

### 📖 详细文档（20 分钟）

3. **验收清单** → `BLOCK1_CHECKLIST.md`
   - 85+ 个验收检查项
   - 功能实现检查
   - 单元测试检查
   - 代码质量检查
   - 安全性检查

4. **完成总结** → `BLOCK1_SUMMARY.md`
   - 交付清单
   - 核心功能详解
   - 测试验证结果
   - 后续扩展点

### 💻 代码示例（10 分钟）

5. **使用示例** → `BLOCK1_USAGE_EXAMPLE.py`
   - 5 个完整的 FastAPI 路由示例
   - 权限检查集成方式
   - 10 个 curl 测试命令
   - 错误处理模式

### ✅ 验证和测试（15 分钟）

6. **单元测试** → `backend/tests/test_dependencies.py`
   - 28 个测试用例
   - 运行：`pytest backend/tests/test_dependencies.py -v`

---

## 🚀 使用流程

### 第 1 步：快速验证（确保环境正常）

```bash
cd framework
python BLOCK1_QUICK_TEST.py
```

**预期**：所有测试通过，显示 `✅ 快速验证完成！`

### 第 2 步：查看核心代码

```python
# 文件：backend/app/dependencies.py
# 核心函数：
# 1. get_current_user()          - 身份验证
# 2. get_direct_reports()        - 下级查询
# 3. check_permission()          - 权限检查
# 4. check_can_view_score()      - 查看成绩权限
# 5. check_can_submit_review()   - 提交评分权限
# 6. get_current_hr()            - HR 专用依赖
# 7. get_current_manager()       - 主管专用依赖
```

### 第 3 步：在 FastAPI 中集成

```python
from fastapi import FastAPI, Depends
from backend.app.dependencies import get_current_user, check_can_view_score
from backend.app.core.db import get_db

app = FastAPI()

@app.get("/api/v1/score/{employee_id}")
async def view_score(
    employee_id: str,
    user = Depends(get_current_user),
    db = Depends(get_db)
):
    # 权限检查（自动拒绝越权）
    check_can_view_score(user, employee_id, db)
    
    # 业务逻辑
    return {"score": 85, "rating": "A"}
```

### 第 4 步：测试权限

```bash
# 有权：普通员工查看自己的成绩
curl -H "Authorization: Bearer emp_token_001" \
     http://localhost:8000/api/v1/score/E001
# → 200 OK

# 无权：普通员工查看他人的成绩
curl -H "Authorization: Bearer emp_token_001" \
     http://localhost:8000/api/v1/score/E002
# → 403 PERMISSION_DENIED

# 越权：缺少 token
curl http://localhost:8000/api/v1/score/E001
# → 401 NOT_AUTHENTICATED
```

### 第 5 步：运行单元测试

```bash
# 运行所有测试
pytest backend/tests/test_dependencies.py -v

# 运行特定测试类
pytest backend/tests/test_dependencies.py::TestCheckCanViewScore -v

# 生成覆盖率报告
pytest backend/tests/test_dependencies.py --cov=backend.app.dependencies
```

---

## 📂 文件结构

```
framework/
├── backend/
│   └── app/
│       ├── dependencies.py                    ← 核心实现（~380 行）
│       ├── exceptions.py                      ← 自定义异常（已有）
│       ├── domain/
│       │   └── models.py                      ← 数据模型（已有）
│       └── tests/
│           └── test_dependencies.py           ← 单元测试（~420 行）
│
├── BLOCK1_QUICK_TEST.py                      ← 快速验证脚本
├── BLOCK1_USAGE_EXAMPLE.py                   ← 使用示例
├── BLOCK1_CHECKLIST.md                       ← 验收清单
├── BLOCK1_SUMMARY.md                         ← 完成总结
├── BLOCK1_QUICK_REFERENCE.md                 ← 快速参考
└── BLOCK1_README.md                          ← 本文件
```

---

## 🎯 核心概念

### 1️⃣ Mock Token 系统

**6 个预定义 token**，模拟 3 种角色的用户：

| Token | 用户 | 角色 | 下级 |
|-------|------|------|------|
| `hr_token_001` | HR001 | HR | - |
| `manager_token_001` | M001 | 主管 | E001, E002 |
| `manager_token_002` | M002 | 主管 | M001, E003 |
| `emp_token_001` | E001 | 普通员工 | - |
| `emp_token_002` | E002 | 普通员工 | - |
| `emp_token_003` | E003 | 普通员工 | - |

**优点**：便于测试各种权限场景，无需真实飞书接口

### 2️⃣ 权限矩阵

**3 种角色 × 8 种操作**：

```
普通员工：只能操作自己（查看、提交自评）
主管：   能操作自己和下级（查看、提交、添加指标）
HR：     无限制（全部操作）
```

### 3️⃣ 遵循 P0-3 原则

- ✅ 身份来自 token（可信）
- ✅ 不信任 URL 参数的 employee_id
- ✅ 权限检查在 L2 业务层（不让 AI 参与）

---

## 🔍 功能详解

### `get_current_user()` - 身份验证

```
步骤：
1. 解析 Authorization header（Bearer token）
2. 从 MOCK_TOKENS 中查询用户信息
3. 从数据库获取 Employee 对象
4. 返回 Employee 对象（用于后续权限检查）

异常：
- NotAuthenticated（401）- token 无效或缺少
```

### `check_can_view_score()` - 查看成绩权限

```
规则：
1. 自己查自己     → 检查 view_own_score 权限
2. 查看所有人     → 检查 view_all_scores 权限（HR）
3. 查看直属下级   → 检查 view_direct_report_score 权限 + 验证 manager_id

返回：
- True           - 有权
- PermissionDenied（403） - 无权
```

### `check_can_submit_review()` - 提交评分权限

```
规则：
1. 自己的自评     → 检查 submit_own_review 权限
2. 下级的评分     → 检查 submit_direct_report_review 权限 + 验证关系

返回：
- AssessmentRecord - 有权（可继续业务逻辑）
- PermissionDenied（403） - 无权
- RecordNotFound（404）  - 记录不存在
```

---

## 🧪 测试用例总览

| 类别 | 测试数 | 覆盖 |
|------|-------|------|
| 身份验证 | 7 | 有效/无效 token，格式错误，缺少 header |
| 权限检查 | 8 | 各角色的权限，权限拒绝 |
| 查看成绩权限 | 5 | 自己/他人，下级/非下级，HR |
| 提交评分权限 | 4 | 自己/他人，权限拒绝，记录不存在 |
| 其他 | 4 | 直属下级查询，依赖函数 |
| **总计** | **28** | **全覆盖** |

---

## 📋 快速检查清单

使用本清单确保正确集成：

- [ ] 快速验证脚本运行成功
- [ ] 所有单元测试通过
- [ ] 核心 4 个函数已导入
- [ ] 所有权限检查已添加
- [ ] 异常处理已配置
- [ ] curl 测试通过（允许和拒绝都测试）
- [ ] 查看了权限矩阵
- [ ] 查看了错误处理示例
- [ ] 理解了 P0-3 原则

---

## 🆘 故障排查

### 问题 1：权限检查返回 403，但应该有权

**检查**：
1. 用户的 token 正确吗？
2. 权限矩阵中该操作允许吗？
3. 权限检查函数放在业务逻辑之前吗？

**解决**：查看 `BLOCK1_QUICK_REFERENCE.md` 的权限速查表

### 问题 2：token 总是返回 NotAuthenticated

**检查**：
1. 用的是 `Bearer emp_token_001` 还是 `emp_token_001`？
2. token 名字拼写正确吗？
3. Authorization header 的格式对吗？

**解决**：查看 `MOCK_TOKENS` 的完整列表

### 问题 3：单元测试失败

**检查**：
1. 数据库是否正确初始化？
2. 测试数据是否完整？
3. 是否有改动原代码？

**解决**：运行 `python BLOCK1_QUICK_TEST.py` 快速诊断

---

## 📞 文档引导

| 需求 | 查看文档 |
|------|--------|
| 快速上手 | BLOCK1_QUICK_REFERENCE.md |
| 理解架构 | BLOCK1_CHECKLIST.md |
| 集成示例 | BLOCK1_USAGE_EXAMPLE.py |
| 验证实现 | BLOCK1_QUICK_TEST.py |
| 完整验收 | BLOCK1_SUMMARY.md |
| 核心代码 | backend/app/dependencies.py |
| 单元测试 | backend/tests/test_dependencies.py |

---

## ✅ 验收标准

本板块完成时满足以下条件：

- ✅ 4 个核心函数实现并测试
- ✅ Mock Token 系统完整（6 个 token）
- ✅ 权限矩阵定义清晰（24 条规则）
- ✅ 单元测试 >= 28 个，全部通过
- ✅ 越权场景 >= 5 个，均被正确拒绝
- ✅ 文档完整（5+ 份）
- ✅ 代码注释详细（每个函数 > 5 行）
- ✅ 架构约束遵守（P0-3 全部满足）

---

## 🚀 下一步

### 完成后的下一个板块

建议顺序：
1. 数据初始化 → 员工、部门数据入库
2. 考核记录管理 → CRUD 操作
3. 证据上传 → 文件处理
4. 评分计算 → 规则引擎集成
5. AI 智能体 → L3 agent-service

### 本板块的后续改进（后期）

- 替换 MOCK_TOKENS 为真实飞书 OAuth
- 实现 token 刷新机制
- 添加审计日志（权限拒绝记录）
- 支持多租户隔离

---

## 📝 更新日志

**2026-11-12** - 板块1 初版完成
- ✅ 4 个核心函数实现
- ✅ 28 个单元测试
- ✅ 5 份文档
- ✅ 快速验证脚本
- ✅ 使用示例

---

**准备好了吗？开始运行 `BLOCK1_QUICK_TEST.py` 吧！** 🚀
