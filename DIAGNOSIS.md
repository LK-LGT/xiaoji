# P0-6 诊断报告：test_grounding_downgrades_when_no_evidence 失败

> 生成时间：2026-11-11 16:45
> 失败位置：`agent-service/tests/test_draft_api.py:136`

---

## 一、失败现象

```python
def test_grounding_downgrades_when_no_evidence():
    """没有证据 → PERF_EVIDENCE 判定为 RISK/UNKNOWN，并产生警告。"""
    payload = _payload()
    payload["evidence"] = []                    # ← 清空证据

    resp = client.post("/v1/agent/draft", json=payload)
    data = resp.json()
    
    # ✅ fairness 正常：PERF_EVIDENCE 从 PASS 降级到 UNKNOWN
    # ❌ gaps 为空：预期有 gap 提示"缺少证据"
    assert data["gaps"]                        # ← 这行失败
```

---

## 二、根本原因

### 问题 A：gaps 的定义逻辑错误

**现在的实现**（main.py:204-211）：

```python
gaps = []
for m in req.metrics:
    if not m.evidence_ids:          # ← 只检查"是否有 evidence_ids 列表"
        gaps.append({...})
```

**问题**：这只捕捉"metric 完全没关联证据"的情况。

**测试场景**：
- payload 创建：metrics 有 `evidence_ids: ["e1"]`
- 测试修改：`payload["evidence"] = []` —— 删除实际证据，但 metrics 的 evidence_ids 仍然指向 "e1"
- 结果：`if not m.evidence_ids` 为 False（因为列表非空），所以 gaps 仍为空
- **但实际上 "e1" 在 evidence 表里不存在，应该标记为缺口**

### 问题 B：缺少"证据存在性校验"

gaps 应该检查三种情况：

1. ❌ metric 没有 evidence_ids（现在有）
2. ❌ metric 的 evidence_ids 列表里的 ID 在 evidence 里不存在（现在**没有**）← 这是测试失败的原因
3. ❌ metric 的证据使用权限是 "reference_only"（待决策②）

---

## 三、修复方案

### 改法：校验 evidence_ids 指向的证据真实存在

**位置**：`framework/agent-service/app/main.py` 第 204-211 行

**改前**：

```python
linked_evidence_ids = {eid for m in req.metrics for eid in (m.evidence_ids or [])}
gaps: list[dict[str, Any]] = []
for m in req.metrics:
    if not m.evidence_ids:
        gaps.append({
            "dimension": m.dimension or m.code,
            "request": f"「{m.dimension or m.code}」缺少可引用的成果证据，请补充具体事例与数据。",
        })
```

**改后**：

```python
# 构建证据目录 —— evidence_id → evidence 对象
evidence_by_id = {e.id: e for e in req.evidence}

gaps: list[dict[str, Any]] = []
for m in req.metrics:
    # 情况 1：没有关联任何证据
    if not m.evidence_ids:
        gaps.append({
            "dimension": m.dimension or m.code,
            "request": f"「{m.dimension or m.code}」缺少可引用的成果证据，请补充具体事例与数据。",
        })
        continue
    
    # 情况 2：关联的证据 ID 在 evidence 里不存在
    missing_ids = [eid for eid in m.evidence_ids if eid not in evidence_by_id]
    if missing_ids:
        gaps.append({
            "dimension": m.dimension or m.code,
            "request": f"「{m.dimension or m.code}」引用的证据 ID {missing_ids} 不存在或已删除，请重新关联。",
        })
        continue

# 所有实际存在且可用的证据
linked_evidence_ids = {eid for m in req.metrics for eid in m.evidence_ids if eid in evidence_by_id}
```

---

## 四、影响范围

| 文件 | 行数 | 改动 |
|---|---|---|
| `agent-service/app/main.py` | 204-211 | 增加存在性校验 |
| `agent-service/tests/test_draft_api.py` | 136 | 测试通过（无需改测试，因为改的是实现，不是测试预期） |

---

## 五、验证方式

修改后跑测试：

```bash
cd framework/agent-service
python -m pytest tests/test_draft_api.py::test_grounding_downgrades_when_no_evidence -v
```

预期输出：

```
test_grounding_downgrades_when_no_evidence PASSED
  缺口提示："「GMV」引用的证据 ID ['e1'] 不存在或已删除，请重新关联。"
```

---

## 六、一句话总结

**gaps 应该检查 evidence_ids 指向的证据是否真实存在，而不仅仅检查 evidence_ids 列表是否为空。**

---

## 七、代码改动（复制粘贴就行）

```python
# 替换 main.py 的 204-211 行

# --- 3. 证据缺口 ---
#    判定标准：某维度**没有关联到任何可引用证据** → 需要补
#    （而不是"评语里没引用" —— 评语是模型写的，不该用它判断证据够不够）

# 构建证据目录 —— evidence_id → evidence 对象
evidence_by_id = {e.id: e for e in req.evidence}

gaps: list[dict[str, Any]] = []
for m in req.metrics:
    # 情况 1：没有关联任何证据
    if not m.evidence_ids:
        gaps.append({
            "dimension": m.dimension or m.code,
            "request": f"「{m.dimension or m.code}」缺少可引用的成果证据，请补充具体事例与数据。",
        })
        continue
    
    # 情况 2：关联的证据 ID 在 evidence 里不存在
    missing_ids = [eid for eid in m.evidence_ids if eid not in evidence_by_id]
    if missing_ids:
        gaps.append({
            "dimension": m.dimension or m.code,
            "request": f"「{m.dimension or m.code}」引用的证据 ID {missing_ids} 不存在或已删除，请重新关联。",
        })

# 所有实际存在且可用的证据
linked_evidence_ids = {eid for m in req.metrics for eid in m.evidence_ids if eid in evidence_by_id}
```

---

**可以改了吗？**
