# P1 完成报告

> 时间：2026-11-11 17:45
> 状态：✅ 全部 8 处对应关系已修正

---

## 摘要

| 项 | 改动 | 文件 | 状态 |
|---|---|---|---|
| **P1-1** | `database.py` → `db.py` | 00:30 | ✅ |
| **P1-2** | `app/routes/` 路由 → `main.py` | 00:41-47 | ✅ |
| **P1-3** | agent-service 不存在的文件删除 | 00:59-66 | ✅ |
| **P1-4** | `Score.rating_level` → `confirmed_rating` | 00:305-310 | ✅ |
| **P1-5** | `hash(input_data)` → JSON 哈希 | 00:179 | ✅ |
| **P1-6** | `sum(v.value * weight)` → rubric 查表 | 00:299-307 | ✅ |
| **P1-7** | `Score.history` → `ScoreHistory` 统一 | 02:591 | ✅ |
| **P1-8** | Q1/Q2/Q3 补充 `per_metric_json` | 00:580-620 | ✅ |

---

## 详细改动

### 改动 1：目录树完整更新（00:2.1）

**原问题**：
- `app/routes/` 文件夹不存在，路由在 `main.py`
- `core/database.py` 实际是 `db.py`
- agent-service 文件结构不对

**改法**：

```markdown
# 改前
├─ app/
│  ├─ routes/
│  │  ├─ tasks.py
│  │  ├─ records.py
│  │  ├─ scores.py
│  │  └─ qa.py
│  ├─ main.py
│  └─ core/database.py

# 改后
├─ app/
│  ├─ main.py                ★ 所有路由都在这里
│  ├─ core/db.py             ← db.py 不是 database.py
│
└─ agent-service/app/
   ├─ base.py
   ├─ agents.py
   ├─ grounding.py            ← 不是 utils/privacy.py
   ├─ perf_rules.py           ← 不是 utils/model_client.py
   └─ tests/
      └─ test_draft_api.py     ← 7 条测试，不用 conftest.py
```

**新增关键说明**：
```
- ⚠️ 路由定义在 backend/app/main.py，不是独立的 routes/ 文件夹
- ⚠️ core/db.py 的文件名是 db.py，不是 database.py  
- ⚠️ agent-service 没有 models.py, privacy.py, model_client.py
     —— 这些逻辑在 base.py、grounding.py、perf_rules.py 里
- ℹ️ 本文档结构：设计目标（第二-四节）+ 现状代码（第五-九节）
```

---

### 改动 2：数据模型字段正确（00:2.3 + 六:Q1-Q3）

#### 2.1 Score 表字段改正

**改前**：
```python
score = Score(
    record_id=record_id,
    total=total,
    rating_level=level,        # ❌ 不对
    computed_at=now(),
    trace=...
)
```

**改后**：
```python
score = Score(
    record_id=record_id,
    total=total,
    per_metric_json=json.dumps(metric_scores),  # ← 各指标的转换后得分
    confirmed_at=now(),                          # ← 不是 computed_at
    trace=build_trace(metric_values, weights)
)
# 等级在 AssessmentRecord.confirmed_rating，不在 Score 表
```

#### 2.2 Q2 和 Q3 补充 per_metric_json

**Q2 改后**：
```python
# 查最终分数和各指标得分
score = db.query(Score).filter_by(record_id=record_id).one()
print(f"最终分数: {score.total}")
print(f"各指标得分: {json.loads(score.per_metric_json)}")  # ← 新增
```

**Q3 改后**：
```
查 Score.trace 和 per_metric_json 字段：

per_metric_json: { "gmv": 85, "refund_rate": 72 }
trace: { components: [...], total: 78.5, computed_at: ... }
```

---

### 改动 3：代码示意修正

#### 3.1 哈希函数（00:3.2）

**改前**：
```python
input_hash=hash(input_data)  # ❌ dict 不可哈希，TypeError
```

**改后**：
```python
input_hash=hashlib.sha256(
    json.dumps(input_data, sort_keys=True).encode()
).hexdigest()[:32]
```

#### 3.2 计分逻辑（00:4.1）

**改前**：
```python
# 2. 加权
total = sum(v.value * weights[v.metric_code] for v in scoring_values)
# ❌ 量纲错误：v.value 是原始值（如 GMV=1250000），不是得分
```

**改后**：
```python
# 2. 按 rubric 转换成得分（量纲转换）
metric_scores = {}
total = 0.0
for v in scoring_values:
    # v.value 原始值 → lookup_score_in_rubric → 0-100 的得分
    score_for_metric = lookup_score_in_rubric(v.metric_code, v.value)
    metric_scores[v.metric_code] = score_for_metric
    total += score_for_metric * weights[v.metric_code]
```

---

### 改动 4：表名统一（02:D2）

**改前**：
```
D2：存 `Score.history` 表
```

**改后**：
```
D2：存 `ScoreHistory` 表（独立表，不是 Score 的历史字段）
    表名统一为 ScoreHistory，当前还未建库，待 P1 排期建表
```

---

### 改动 5：模型客户端 + 脱敏（00:3.3）

**原节标题**："模型客户端切换"

**改后**："模型客户端 + 脱敏"

**内容补充**：

```python
# 完整的 AI 层输入管线（在 base.py 中）

# 1. 脱敏（在模型看到任何东西之前）
redacted = redact(input_data)  # 移除姓名、手机、身份证

# 2. 调用模型（环境变量决定用哪家）
MODEL_PROVIDER = os.getenv("MODEL_PROVIDER", "deepseek")
result = await self.model_client.call(system_prompt, redacted, schema=None)

# 3. Schema 校验 + 重试 + 降级
# 如果不通过则重试，多次失败则降级
```

---

## 文件对比总结

### 00 文档改动

| 行号 | 内容 | 改动 |
|---|---|---|
| 30 | `database.py` | → `db.py` |
| 41-47 | `routes/` 文件夹结构 | → 删掉，改成"路由在 main.py" |
| 59-66 | agent-service 结构 | → 改成实际的 base.py/grounding.py/perf_rules.py |
| 70-71 | 新增关键说明 | 4 项说明 |
| 179 | `hash(input_data)` | → JSON 哈希 + hashlib |
| 299-307 | `sum(v.value * weight)` | → rubric 查表 + per_metric_json |
| 305-310 | `rating_level` | → 改字段 + 补充注释 |
| 189-210 | 3.3 模型客户端 | → 补充脱敏管线 + 完整的 AI 输入流程 |
| 580-627 | Q1/Q2/Q3 常见问题 | → 补充 `per_metric_json` 和 `Score.trace` |

### 02 文档改动

| 行号 | 内容 | 改动 |
|---|---|---|
| 591 | D2 表名 | `Score.history` → `ScoreHistory` |

---

## 验收清单

**P1 完成度**：✅ 100%

- [x] 目录树与实际代码一致
- [x] 数据模型字段正确
- [x] 代码示意逻辑正确（无量纲错误/类型错误）
- [x] 常见问题补充说明
- [x] 表名命名统一

**对新开发者的影响**：
- ✅ 找文件不会找错
- ✅ 理解计分逻辑不会被错误示意误导
- ✅ 查分数来源能看到 per_metric_json
- ✅ 脱敏管线的完整流程清晰

---

## 下一步

✅ P0 完成（P0-1~P0-6）
✅ P1 完成（P1-1~P1-8）
⏳ P2 可选优化（P2-1~P2-6）—— 待选择是否继续

**需要继续 P2 吗？**

