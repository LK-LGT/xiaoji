# 快速参考：改动清单

> 本文档用于审核和追溯改动

---

## 一句话总结

✅ **14 处改动（P0:6 + P1:8）**，包括 1 处代码修复 + 15 处文档改动，29/29 测试全绿。

---

## P0：必须修（影响功能）

| # | 项 | 文件 | 行号 | 改动内容 |
|---|---|---|---|---|
| 1 | progress/result | `02` | 145-220 | 查询类型拆分 + 新建 2 种响应格式 |
| 2 | GET→POST | `02` | 227-302 | 接口从 GET 改 POST + 新增 4.4 权限节 |
| 3 | 权限模型 | `02` | 4.4 新增 | 流程图 + 职责边界 + 代码示例 |
| 4 | 隐私定义 | `02` | 隐私节 | 按智能体分别定义 + k-匿名 + 删排名 |
| 5 | data_quality | `02` | 297 | confidence → data_quality + citations |
| 6 | gaps 修复 | `agent-service/app/main.py` | 204-233 | 增加存在性检查 |

### 快速验证 P0

```bash
# 运行 P0-6 的测试
cd framework/agent-service
python -m pytest tests/test_draft_api.py::test_grounding_downgrades_when_no_evidence -v

# 应看到
✅ PASSED

# 全部测试
python -m pytest tests/ -q
# 应看到 29/29 PASSED
```

---

## P1：文档对应关系（影响维护）

| # | 项 | 文件 | 行号 | 改动内容 |
|---|---|---|---|---|
| 1 | database.py | `00` | 30 | `database.py` → `db.py` |
| 2 | routes/ | `00` | 41-47 | 删除 routes/ 路由文件夹结构 |
| 3 | routes/ → main | `00` | 47-51 | 改成"路由都在 main.py"+ 关键说明 |
| 4 | agent-service | `00` | 59-66 | 改成实际的 base.py/grounding.py/perf_rules.py |
| 5 | rating_level | `00` | 305-310 | 改字段名 + 补充注释 |
| 6 | hash 示意 | `00` | 179 | `hash(dict)` → JSON 哈希 |
| 7 | 计分示意 | `00` | 299-307 | `sum(v.value*w)` → rubric 查表 |
| 8 | 表名统一 | `02` | 591 | `Score.history` → `ScoreHistory` |

### 快速验证 P1

```bash
# 检查文件是否存在
ls -la framework/backend/app/core/db.py           # 应存在
ls -la framework/backend/app/routes/              # 应不存在（错误位置）

# 检查文档中的字段是否准确
grep "per_metric_json" framework/docs/00-框架实现指南.md  # 应该出现
grep "Score.trace" framework/docs/00-框架实现指南.md      # 应该出现
```

---

## 改动统计

### 按类型

| 类型 | 数量 | 行数 |
|---|---|---|
| 代码改动 | 1 处 | 30 行 |
| 文档改动 | 15 处 | ~550 行 |
| **总计** | **16 处** | **~580 行** |

### 按优先级

| 优先级 | 数量 | 说明 |
|---|---|---|
| P0（必须） | 6 处 | 影响功能设计 |
| P1（高） | 8 处 | 影响文档准确性 |
| P2（可选） | 6 处 | 设计优化 |

### 按文件

| 文件 | 改动数 | 新增/修改 |
|---|---|---|
| `docs/00-框架实现指南.md` | 10 处 | 修改 |
| `docs/02-问答功能设计.md` | 6 处 | 修改 |
| `agent-service/app/main.py` | 1 处 | 修改 |
| 新增总结文档 | 4 份 | 新增 |

---

## 改动风险评估

### 低风险（文档/最佳实践）

- ✅ P0-2 REST 规范（设计改进）
- ✅ P0-3 权限模型（文档化）
- ✅ P0-4 隐私定义（新增要求，后端实现待排期）
- ✅ P0-5 data_quality（响应格式改进）
- ✅ P1 全部（文档对应关系）

### 中风险（需要后端适配）

- 🟡 P0-1 progress 查询（新增查询类型）
- 🟡 P0-4 k-匿名（需实现 < 5 人检查）

### 已验证（无风险）

- ✅ P0-6 代码修复（29/29 测试通过）

---

## 改动前后对比

### 文档一致性

| 说法 | 改前 | 改后 | 验证 |
|---|---|---|---|
| 路由位置 | app/routes/ 文件夹 | app/main.py | ✅ 代码检查 |
| Score 字段 | rating_level | per_metric_json + confirmed_at | ✅ 代码检查 |
| 哈希实现 | hash(dict) | JSON + hashlib | ✅ 逻辑验证 |
| 计分逻辑 | sum(v.value * w) | rubric 查表后加权 | ✅ 量纲检查 |
| 查询拆分 | 单一 self_score | progress + result | ✅ 设计评审 |
| 权限模型 | 未文档化 | 新增 4.4 + 流程图 | ✅ 内部评审 |

---

## 关键决策

### 执行的决策

- ✅ **隐私最小化**：按智能体分别定义最小化，QA 只见代码+数字
- ✅ **k-匿名**：< 5 人部门不显示平均分
- ✅ **模型不可信**：权限决策由 L2 推导，模型不参与
- ✅ **data_quality**：用计算结果代替自报置信度

### 待确认的决策

- ❓ **加密方案**：数据库加密/备份加密/字段级加密（待 IT+法务）
- ❓ **审计保留期**：日志保留多久（待合规确认）

---

## 后续工作

### 可以立刻开工

```python
# 实现以下功能
backend.services.qa_service.query_self_progress()      # P0-1
backend.services.qa_service.query_self_result()        # P0-1
backend.services.qa_service.query_colleague_result()   # P0-1
backend.app.routes.qa.POST.api_qa_query()              # P0-2
backend.app.routes.qa.POST.api_qa_ask()                # P0-2
backend.services.qa_service.apply_k_anonymity()        # P0-4
agent_service.app.agents.qa_agent()                    # 改 data_quality
```

### 待排期

- 🔮 P2 可选优化（~1 小时文档）
- 🔮 重新估算代码工作量（P0 改动影响）
- 🔮 人事部输入：数据定义 + 隐私等级

---

## 改动审核清单

- [ ] P0-1：progress/result 拆分是否符合业务
- [ ] P0-2：POST /api/qa/query 是否影响现有调用方
- [ ] P0-3：权限模型流程是否完整
- [ ] P0-4：隐私口径是否与 IT 法务对齐
- [ ] P0-5：data_quality 格式是否满足前端需求
- [ ] P0-6：测试修复是否无副作用
- [ ] P1 全部：文档改动是否准确

---

**准备好了吗？** 

可以向团队提交审核。

