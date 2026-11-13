# 员工绩效管理智能体 —— 框架代码骨架

> 日期：2026-11-11
> 依据：《员工绩效管理智能体-框架设计-20261111.md》
> 状态：**可运行**。核心链路（发起考核 → 采集数据 → 自评 → 算分 → AI 草稿 → 确认）已端到端跑通

---

## 📖 文档导航

**第一次来？选择你的路径**：

| 我想... | 看这个 | 时间 |
|---|---|---|
| **跑起来试试** | [快速开始](#快速开始) | 5 分钟 |
| **理解架构设计** | `docs/01-架构与决策.md` | 20 分钟 |
| **学会怎么改代码** | `docs/00-框架实现指南.md` | 30 分钟 |
| **了解 Q&A 功能** | `docs/02-问答功能设计.md` | 20 分钟 |
| **看业务流程** | `docs/03-操作手册.md` | 15 分钟 |

---

## 快速开始

```bash
# 依赖
pip install fastapi "uvicorn[standard]" sqlalchemy pydantic pydantic-settings httpx pytest

# 终端 1 —— 启动智能体服务（端口 8800）
cd framework/agent-service
python -m uvicorn app.main:app --port 8800

# 终端 2 —— 跑端到端演示
cd framework/backend
python scripts/demo_e2e.py

# 跑测试（22 条，覆盖四条不可破原则）
python -m pytest tests/test_framework.py -q
```

---

## 目录结构

```
framework/
├─ backend/                       L2 业务层 —— 唯一事实源
│  ├─ app/
│  │  ├─ core/                    配置 + 数据库
│  │  ├─ domain/
│  │  │  ├─ enums.py              全部状态机枚举
│  │  │  └─ models.py             SQLAlchemy 模型（8 个域）
│  │  ├─ services/
│  │  │  ├─ scoring.py            ★ 规则引擎 —— 分数的唯一产地
│  │  │  ├─ config_service.py     ★ 决策③ 三道保险（校验/快照/兜底）
│  │  │  ├─ evidence_service.py   ★ 决策② 考勤仅参考的物理保证
│  │  │  └─ review_service.py     ★ 考核编排 + Score/RatingDraft 隔离
│  │  ├─ db/seed.py               种子（运营部试点）
│  │  └─ main.py                  业务 API
│  ├─ scripts/demo_e2e.py         端到端演示
│  └─ tests/test_framework.py     22 条测试
│
└─ agent-service/                 L3 智能体层
   └─ app/
      ├─ base.py                  ★ 智能体基类（脱敏→校验→重试→降级→留痕）
      ├─ agents.py                六个智能体，按模型不可信程度分层
      └─ main.py                  FastAPI + /v1/agent/draft 契约
```

---

## 分层与职责

```
┌────────────────────────────────────────────────────────┐
│ L0 接入层  飞书（审批/考勤/多维表格/机器人）+ 经营系统       │
├────────────────────────────────────────────────────────┤
│ L1 交互层  PC 后台 · H5 动态表单                          │
├────────────────────────────────────────────────────────┤
│ L2 业务层  ★ 唯一事实源 · 数字只在这里产生                 │
│           backend/                                        │
├────────────────────────────────────────────────────────┤
│ L3 智能体层  ★ 不算分 · 不审批 · 不自动生效               │
│           agent-service/                                  │
├────────────────────────────────────────────────────────┤
│ L4 模型层  DeepSeek / 通义（OpenAI 兼容，可切换）           │
└────────────────────────────────────────────────────────┘
横切面：身份权限 · 审计 · PII 脱敏 · 幂等 · 降级 · 评测
```

---

## 四条不可破原则 → 代码落点

### 原则三：模型永远不产生数字

| 保证 | 实现 |
|---|---|
| 分数只由规则引擎产出 | `services/scoring.py` —— 纯确定性，不 import 任何模型客户端 |
| AI 建议与分数物理隔离 | `Score` 表只由 `compute_score()` 写入；AI 只能写 `RatingDraft`/`CommentDraft` |
| 响应契约无 score 字段 | `agent-service/app/main.py` 的 `DraftResponse` 里**没有** score |
| 人工确认后重算 | `confirm_draft()` 重新跑一遍规则引擎（确认的等级可能与 AI 建议不同） |

测试：`test_ai_draft_never_writes_score`（AI 返回 `score: 999` 被忽略）、`test_score_is_reproducible_without_ai`

### 决策②：考勤六类异常只作参考

| 保证 | 实现 |
|---|---|
| 考勤进不了算分集 | `add_metric_value()` 对考勤来源直接抛 `AttendanceEvidenceRejected` |
| 六类全部展示 | `AttendanceAnomalyType` 六值枚举，`record_attendance_anomalies()` 全部写入 |
| 配套标注 | 请假标"已审批"，加班标"仅参考、不参与评分"，视图带免责声明 |
| 模型无法把它当支撑 | 请求体里 `attendance_anomalies` 是**独立字段**，不混进 `evidence` |

测试：`test_attendance_cannot_enter_scoring_set`、`test_attendance_does_not_change_score`、`test_attendance_not_in_agent_evidence_field`

### 决策③：多维表格三道保险

| 保险 | 实现 |
|---|---|
| 读入校验 | `validate_config()` —— 权重合计=100、指标 ID 唯一、口径字段非空、引用完整性 |
| 冻结快照 | `freeze_snapshot()` —— 任务启动时复制配置进 `ConfigSnapshot` |
| 失败兜底 | 读取失败 → 沿用上次快照 + 记 `ConfigSyncLog` + 告警 |

测试：`test_config_change_does_not_affect_running_task`（中途改权重，分数不变）

### 原则四：可降级、不阻塞

| 智能体 | 降级行为 |
|---|---|
| 填报辅导 | 不追问，只列必填项 |
| 证据校验 | 标记"待人工判断"，进主管队列 |
| 评级建议 | 不给建议，只给原始指标表 |
| 总结 | 模板化评语 + 指标表 |
| 校准 | 只出对比表 |
| 问答 | 提示不可用，指路到后台报表 |

两层降级：**智能体服务挂了** → 业务层出模板草稿（`generate_draft` 的 try/except）；**单个智能体挂了** → `BaseAgent.run()` 重试后降级。

测试：`test_agent_downtime_degrades_but_flow_continues`

---

## 智能体分层

按**模型的不可信程度**分层，规则越硬越靠下：

| 层 | 智能体 | 模型做什么 | 模型不做什么 |
|---|---|---|---|
| 交互层 | `fill_coach` | 生成追问、解释口径 | 不改表单定义、不判定完成 |
| 判别层 | `evidence` | 判证据是否充分 | 不写结论、不给分 |
| 判别层 | `rating` | 给"指标值+目标"定级并说明理由 | **不乘权重、不算总分** |
| 生成层 | `comment` | 写评语、简报、月报 | 不产生数字 |
| 观察层 | `calibration` | 发现松紧异常并提示 | 不给结论、不改分 |
| 咨询层 | `qa` | 查数、对比、找下降指标 | 不写库、不改状态 |

共用管线（`base.py`）：脱敏 → 调用 → Schema 校验 → 重试 → 降级 → 留痕。

---

## 接口契约

### `POST /v1/agent/draft`（智能体层）

```jsonc
// 请求
{
  "task_id": "T-2026-10-运营部",
  "employee": { "alias": "该员工", "role_title": "运营专员" },
  "period": "2026-10",
  "metrics": [                      // 已算好的值 + 已查好的等级
    { "code": "gmv", "dimension": "GMV",
      "value": 1250000, "target": 1200000,
      "ratio": 1.0417, "ratio_level": "B",   // ← 业务层算的，模型只复述
      "level_scale": [...], "evidence_ids": ["e1"] }
  ],
  "evidence": [ { "id": "e1", "kind": "feishu_approval", "summary": "...", "usage": "scoring" } ],
  "attendance_anomalies": [ { "type": "late", "count": 3, "disclaimer": "考勤异常不自动扣分，仅作参考" } ],
  "rubric": [...]
}
```

```jsonc
// 响应 —— 注意：没有 score 字段
{
  "ratings": [ { "dimension": "GMV", "level": "B", "reason": "...", "evidence_ids": ["e1"] } ],
  "comment_draft": "## 本月总结\n...",
  "citations": ["e1"],
  "gaps": [ { "dimension": "退款率", "request": "请补充..." } ],
  "fairness": { "grounded": true, "warnings": [], "unsupported_claims": 0 },
  "degraded": false,
  "agent_status": { "rating": "ok", "comment": "ok" }
}
```

### 业务层主要接口

| 接口 | 作用 |
|---|---|
| `POST /api/tasks` | 建任务 + 冻结配置快照 |
| `POST /api/tasks/{id}/open-records` | 按范围生成员工记录 |
| `POST /api/records/{id}/self-review` | 提交自评（幂等；双入口互斥） |
| `POST /api/metric-values` | 写指标实测值（算分集唯一入口） |
| `POST /api/attendance/anomalies` | 写考勤异常（reference_only） |
| `POST /api/records/{id}/draft` | 生成 AI 草稿（自动降级） |
| `POST /api/records/{id}/score/recompute` | **复算分数** |
| `POST /api/records/{id}/confirm` | 人工确认（最后一道闸） |
| `GET /api/records/{id}/citations/validate` | 校验证据引用有效性 |

---

## 领域模型（8 个域）

```
组织域   Department · Employee
配置域   Metric · WeightSet · Rubric · FormTemplate · ConfigSnapshot · ConfigSyncLog
考核域   AssessmentTask · AssessmentRecord · SelfReview · ManagerReview · Appeal · ApprovalEvent
证据域   MetricValue（算分集） · Evidence（统一证据，usage 区分）
输出域   Score（唯一真值） · RatingDraft · CommentDraft · Rollup
治理域   AiTrace · AuditLog
```

关键字段：

- `Evidence.usage` = `scoring` | `reference_only` —— 决策②的物理落点
- `Evidence.visibility` —— 员工授权（沿用 reviewops 的 consent gate）
- `ConfigSnapshot` —— 决策③第二道保险
- `Score` vs `RatingDraft` —— 两条独立状态机，物理隔离

---

## 下一步

| 优先级 | 事项 |
|---|---|
| **P0** | 接飞书：应用权限、通讯录同步、三套审批定义、事件回调 + 轮询兜底 |
| **P0** | 指标定义表落地（口径、来源、责任人）—— 决策⑤明确前移到 D1–2 |
| **P0** | 接真实模型（DeepSeek/通义），替换 `EchoModelClient` |
| **P1** | 汇总与校准（三级：员工→部门→公司）、导出任务 |
| **P1** | 申诉流程与争议裁决 |
| **P1** | 飞书多维表格 `BitableProvider` 落地（`LocalProvider` 已是兜底） |
| **P2** | H5 动态表单渲染（决策④ v1.5）+ 免登 |
| **P2** | Vue 3 前端（PC 后台） |

### 框架级待决（承接《框架设计》第 10 节）

N1 `enterprise-plan-results` 授权 · N3/N4 前后端栈确认 · N5/N6 考勤取数与补卡回溯窗口

---

## 与 01 资产的关系

| 01 项目 | 本框架取用 |
|---|---|
| `reviewops-agent` | 智能体骨架、证据卡片、脱敏管线、置信度路由、人工确认闸门、评测框架 |
| `enterprise-plan-results` | **仅作设计参考**（无 LICENSE，优先不复制代码）：AI 治理、评分规则、申诉裁决、导出任务、数据范围守卫 |
| `wukong-hrm` | **仅读业务口径**，不复制代码（悟空协议需商业授权） |
| `employee-performance-ai` | 不采用 |
