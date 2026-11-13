# 🚀 BCD 优化并行计划

**启动时间**：2026-11-12 12:27 UTC+8  
**目标**：优化现有代码 + 并行推进 + 性能测试

---

## 📋 行动计划概览

```
B. 优化现有代码         (1-2h)
   ├─ 修复 pytest fixture
   ├─ 添加更多测试用例
   └─ 完善错误处理

C. 并行推进三个板块      (需要优先级)
   ├─ 【板块3.1】自评接口
   ├─ 【板块3.2】评分接口
   ├─ 【板块4】规则引擎
   └─ 【板块5】AI 智能体

D. 性能测试              (1-2h)
   ├─ 1000+ 员工压力测试
   ├─ 数据库查询优化
   └─ 性能基准测试
```

---

## 🔧 B. 优化现有代码

### B1. 修复 pytest fixture

**问题**：SQLite 重复键约束

**解决方案**：

```python
@pytest.fixture(autouse=True)
def cleanup_db(db: Session):
    """清理测试数据"""
    yield
    # 测试后清理
    db.query(Department).delete()
    db.query(Employee).delete()
    db.query(Metric).delete()
    db.commit()
```

**预计工作量**：30 分钟

### B2. 添加更多测试用例

**新增测试**：

1. test_concurrent_task_creation - 并发创建测试
2. test_large_employee_batch - 大批量员工测试
3. test_database_consistency - 数据一致性测试
4. test_error_recovery - 错误恢复测试

**预计工作量**：1 小时

### B3. 完善错误处理

- ✅ 添加日志记录
- ✅ 改进异常信息
- ✅ 添加重试机制

**预计工作量**：30 分钟

---

## 🔄 C. 并行推进三个板块

### 推荐优先级

```
第一阶段（Week 1）：
  【板块3.1】自评接口        ← 最高优先级（依赖最少）
  【板块3.2】评分接口        ← 高优先级

第二阶段（Week 2）：
  【板块4】规则引擎          ← 中优先级（依赖3完成）
  
第三阶段（Week 3）：
  【板块5】AI 智能体         ← 低优先级（可选）
```

### 并行架构

```
【板块3】自评/评分
  └─ POST /v1/assessment/records/{id}/self-review
  └─ POST /v1/assessment/records/{id}/manager-review

【板块4】规则引擎
  └─ POST /v1/scores/calculate
  └─ GET /v1/scores/{id}

【板块5】AI 智能体
  └─ POST /v1/ai/suggestions
  └─ POST /v1/ai/summary
```

---

## 📈 D. 性能测试

### D1. 压力测试

```bash
# 创建 1000+ 员工的考核任务
curl -X POST http://localhost:8000/v1/assessment/tasks \
  -H "Authorization: Bearer hr_token" \
  -d '{
    "title": "大规模考核",
    "target_employee_ids": ["E001", "E002", ..., "E1000"],
    ...
  }'

# 测试指标：
# - 响应时间 (目标 < 2s)
# - 内存使用 (目标 < 500MB)
# - 数据库连接数 (目标 < 10)
```

**预计工作量**：1 小时

### D2. 数据库优化

```sql
-- 添加索引
CREATE INDEX idx_assessment_task_period ON assessment_task(period);
CREATE INDEX idx_assessment_record_status ON assessment_record(status);
CREATE INDEX idx_assessment_record_employee ON assessment_record(employee_id);

-- 查询优化
EXPLAIN QUERY PLAN SELECT ...
```

**预计工作量**：1 小时

### D3. 性能基准

| 操作 | 目标 | 实际 | 状态 |
|------|------|------|------|
| 创建 Task | < 500ms | ? | 📊 测试中 |
| 生成 100 Records | < 1s | ? | 📊 测试中 |
| 查询 Records | < 200ms | ? | 📊 测试中 |

---

## ✅ 立即可做

选择你想要我按顺序完成的方向：

1. **先完成 B（优化现有代码）** - 稳定基础
2. **然后启动 D（性能测试）** - 发现瓶颈
3. **并行设计 C（多板块）** - 分工协作

---

## 📊 时间表

```
现在 → B (1-2h) → D (1-2h) → C 设计阶段 (2-3h)
│
├─ 总耗时：5-7 小时
├─ 最快完成：今天下午
└─ 可部署：明天
```

准备开始？
