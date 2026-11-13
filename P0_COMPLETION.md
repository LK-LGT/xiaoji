# P0 完成报告

> 时间：2026-11-11 17:15
> 状态：✅ 全部 6 处 P0 已完成并验证

---

## 摘要

| 项 | 状态 | 验证 |
|---|---|---|
| **P0-1** progress/result 拆分 | ✅ | 02 文档已改，查询类型枚举 + 响应示例分离 |
| **P0-2** GET → POST 改造 | ✅ | 02 文档已改，支持复杂参数 |
| **P0-3** 权限模型（模型不可信） | ✅ | 02 文档加 4.4 节，流程图 + 职责边界 |
| **P0-4** 隐私口径（分别定义） | ✅ | 新增隐私部分 + 删排名 + k-匿名 + 加密改为待确认 |
| **P0-5** confidence → data_quality | ✅ | 响应格式已改 |
| **P0-6** 测试修复 | ✅ | gaps 逻辑完善，29/29 测试全绿 |

---

## 一、P0-1：progress vs result 拆分

**改动**：

| 位置 | 原来 | 现在 |
|---|---|---|
| 4.1 查询枚举 | `self_score` 一个类型 | `self_progress` + `self_result` 分离 |
| 响应示例 ① | 个人分数（月末） | 月中进度（MetricValue 源） |
| 响应示例 ② | 改成新增类型 | 月末成绩（Score + confirmed_rating） |

**关键字段**：

```json
// progress —— 月中随时查
{
  "type": "self_progress",
  "as_of": "2026-10-15",
  "completed_weight": 0.7,      // 新增
  "missing_weight": 0.3,         // 新增
  "note": "满意度预计 10 月 20 日出数"
}

// result —— 月末查
{
  "type": "self_result",
  "status": "finalized",
  "confirmed_by": "李主管",      // 新增
  "confirmed_at": "2026-10-31T18:00:00Z"
}
```

---

## 二、P0-2：REST 规范改造

**改动**：

```
GET /api/qa/query (❌ 旧)
    ↓
POST /api/qa/query (✅ 新)
```

**原因**：GET 带 JSON body 不规范，部分网关会丢弃

**参数支持**：

```json
{
  "type": "self_progress",
  "period": "2026-10",
  "query_params": { "employee_id": "E001" }
}
```

---

## 三、P0-3：权限模型 + 模型不可信

**新增 4.4 节**，核心内容：

### 关键原则

**模型输出绝不可信**：
- ❌ 模型生成的 `query_type` 值 → 只信白名单
- ❌ 模型生成的 `employee_id` / `department_id` → L2 推导
- ✅ 模型只做 NLU 映射到枚举，映射失败就澄清问题

### 正确流程

```python
1. 从 token 解出身份        # user = get_current_user(token)
2. 模型映射到白名单          # query_type = model.map_to_enum(question)
3. L2 推导数据范围          # employee_id = get_direct_reports(user.id)
4. 权限校验                  # if target in direct_reports: OK
5. 查询数据库                # data = db.query(...)
6. 调用 AI（已校验数据）     # answer = ai_agent.qa(question, data)
```

### 职责边界表

| 模型能做 | 模型不能做 |
|---|---|
| 理解"查我的分数" → `self_result` | 生成 employee_id |
| "请问你是查 A 部门还是 B 部门？" | 假设一个 department_id |
| 解释数据和提建议 | 做权限决策 |

---

## 四、P0-4：隐私数据最小化

### 4.1 按智能体分别定义

**问答智能体（QA）—— 只见代码 + 结构化数据**

```json
{
  "user_id": "E001",              // ← code, not "张三"
  "score": 78.5,
  "components": [...]
}
```

**填报辅导智能体（FillCoach）—— 必须见自评文本**

```python
{
  "employee_id": "E001",
  "self_review_text": "...",      # 脱敏后的原文
  "trace_id": "audit-123"         # 留痕
}
```

### 4.2 删除排名查询

**原**："本月谁表现最好/最差"

**现**：删掉排名，改成"A 等级有多少人"（只看分布，不排名）

### 4.3 k-匿名（小部门保护）

**新增 D1 决策**：

```json
{
  "department_result_summary": {
    "total_employees": 3,
    "average_score": null,         // ← < 5 人隐藏
    "note": "样本过小，隐私保护中不显示平均分"
  }
}
```

### 4.4 加密改为待确认

**原**："HTTPS + 权限检查，不需要额外加密"

**现**：

```
当前实现 ✅：
  - HTTPS
  - 权限检查
  - 审计日志

待确认 ❓：
  - 数据库加密
  - 备份加密
  - 密钥管理
  - 字段级加密
  - 日志保留期限
```

> 第一期交付权限 + 审计，合规意见回来后单独排期加密

---

## 五、P0-5：confidence → data_quality

**改动**：

```json
// 旧
{
  "answer": "...",
  "confidence": 0.95           // ❌ 模型自报，没意义
}

// 新
{
  "answer": "...",
  "data_quality": {
    "completeness": 0.95,      // ✅ L2 计算（已出数权重/总权重）
    "as_of": "2026-10-31",
    "degraded": false,
    "citations": [
      "Score.total",
      "MetricValue.gmv"
    ]
  }
}
```

**为什么改**：

- `confidence` 由模型自报，用户会误信
- `data_quality` 由 L2 计算，可验证
- `citations` 明确数据来源，便于审计

---

## 六、P0-6：测试修复

### 问题诊断

```python
# 原逻辑
gaps = []
for m in metrics:
    if not m.evidence_ids:  # ❌ 只检查列表是否为空
        gaps.append(...)

# 测试场景：evidence = [] 但 evidence_ids = ["e1"] → gaps 为空（bug）
```

### 修复方案

```python
# 改后逻辑
evidence_by_id = {e.id: e for e in req.evidence}
gaps = []
for m in metrics:
    if not m.evidence_ids:
        gaps.append(...)
    
    missing_ids = [eid for eid in m.evidence_ids if eid not in evidence_by_id]
    if missing_ids:  # ✅ 新增：检查指向的证据是否存在
        gaps.append(...)
```

### 验证结果

```
✅ test_grounding_downgrades_when_no_evidence → PASSED
✅ agent-service 全部 7 个测试 → PASSED
✅ backend 全部 22 个测试 → PASSED
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
✅ 合计：29/29 测试全绿
```

---

## 七、文件清单

| 文件 | 改动 |
|---|---|
| `docs/02-问答功能设计.md` | ✅ 全 5 处改完 |
| `agent-service/app/main.py:204-233` | ✅ gaps 逻辑完善 |
| `CHECKLIST.md:P0-6` | ✅ 标记完成 |
| `DIAGNOSIS.md` | 📄 生成（诊断报告） |

---

## 八、下一步行动

### 现在（立刻）

- [x] P0-1 progress/result 拆分
- [x] P0-2 GET → POST
- [x] P0-3 权限模型
- [x] P0-4 隐私口径
- [x] P0-5 data_quality
- [x] P0-6 测试修复

### 本周

- [ ] P1：文档对不上代码的 8 处修正
- [ ] P1：改 README / 00 框架指南

### 下周

- [ ] P2：可选设计优化
- [ ] 排期重新定义（人事部输入为 D0）

---

## 九、验收清单

**P0 完成度**：✅ 100%

- [x] 所有代码改动已验证（29 个测试全绿）
- [x] 所有文档改动已完成（02 文档 5 处 P0 已改）
- [x] 没有破坏现有功能（回归测试通过）
- [x] 符合九项决策和四条原则

**可以开工吗**？✅ 是的，P0 全部就绪

---

**下一步**：继续 P1 的 8 处文档与代码对不上的地方？还是先让你审一下改动？

