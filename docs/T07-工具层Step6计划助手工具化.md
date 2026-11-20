# T07 —— 工具层 Step 6：计划助手工具化

> 状态：**已完成并已在真模型 + 真实库上端到端验证**
> 完成时间：2026-11-20
> 关联：`docs/T05-工具层能力扩展路线图.md`（批次 D）

---

## 一、这解决什么问题

员工写月/周/日计划时，AI 助手是**空的**：

| 操作 | 改造前 | 改造后 |
|---|---|---|
| 点「AI 生成周计划」 | 弹个空框，`items: []` | 返回**可编辑的计划条目**（任务名/内容/交付物/质量标准/起止日期） |
| 点「AI 上下文」 | `enabled: false` | 返回**真实**的历史计划数量、可参考的上下文类型、缺什么信息 |
| 点「AI 检查月计划」 | 空 `issues: []` | 返回**逐条体检报告**：与上月语义重复 / 缺交付物 / 时间重叠 |
| 上传「计划示例文件」 | `content: ""` | 真解析出原文（含 GBK 编码），让 AI 学用户的写法风格 |
| 「按这条建议改一下」 | 空 | 真的按指令调整草稿 |

**业务价值**：员工写一份月计划从 1~2 小时降到 10 分钟改改；
主管审批前的「体检」从人工翻变成逐条提示。

---

## 二、为什么叫「工具化」而不是「接上模型」

P3-2 已经把真模型接进来了（`deepseek-flash`）。但**模型自己不知道数据库里有什么**。

Step 6 做的是给计划助手装上「自己查数据」的能力：

```
员工点「AI 生成周计划」
        ↓
backend /v1/employee/ai/week-plans/generate
        │  签发工具令牌（allow 白名单 + scope）
        ↓
agent-service /v1/agent/plan
        │  模型自主调工具
        ↓
backend /v1/plan-tools/get_my_plan_history
backend /v1/plan-tools/get_department_plan_history
        │  返回真实历史计划
        ↓
模型基于真实数据生成草稿 → 清洗（去分数泄漏）→ 返回
        ↓
前端展示草稿 → **员工确认后**才落库
```

---

## 三、关键设计决策

### 3.1 新建独立工具网关（不复用 `/v1/agent-tools/*`）

现有 3 个 L2 工具**全部绑死在考核任务上**：

- 令牌里的 `task` 必须是真实存在的 `AssessmentTask`；
- `period` 必须与任务周期一致，否则 400。

计划助手场景**没有考核任务** —— 员工写 11 月月计划时，
11 月的考核任务可能压根不存在。所以新建 `/v1/plan-tools/*`：

| 端点 | 工具名 | 能看到 |
|---|---|---|
| `POST /v1/plan-tools/get_my_plan_history` | `get_my_plan_history` | **只有我自己**的历史计划 |
| `POST /v1/plan-tools/get_department_plan_history` | `get_department_plan_history` | **同部门**历史计划（脱敏） |

令牌里用哨兵 `task_id="plan"`（计划工具不查考核任务）。

### 3.2 部门可见 ≠ 可原样给模型

用户选了「我的历史 + 部门同级同事的历史计划」。但**同部门的计划**仍含他人信息，
所以部门级工具做了三层收敛：

1. **不返回 owner_id / owner_name** —— 只用 `authorAlias`（如「同部门同事A」），
   模型能说「部门里常见的做法是……」，但**不能指名道姓**。
2. **不返回审批意见** —— 那是主管对**个人**的评语，
   含个人信息，且会诱导模型生成针对具体个人的评价。
3. **不返回 performance_weight / estimated_value** ——
   这两个是**考核数据**，不是计划参考。

但**任务名称、内容、交付物必须保留** —— 那正是「参考历史计划」的意义。
只是把文本里可能出现的人名做了替换。

### 3.3 人名脱敏：宁可漏，不可坏

中文人名识别有个硬问题：「路」「查」「成」「季」都是真实姓氏，
纯靠「姓 + 1~2 字」会把「排查线上故障」误判成「排某同事故障」——
**损坏业务语义比漏脱敏更糟**。

最终规则：

| 优先级 | 规则 | 例子 |
|---|---|---|
| 1 | 职务称谓**无条件**替换 | 「李经理」→「相关同事」 |
| 2 | 常见业务词**保护**（不参与匹配） | 「稳定性」「完成方案」原样保留 |
| 3 | 有**指人标记**才替换 | 「与王五对接」→「与相关同事对接」 |
| 4 | 裸名（前后无标记）也替换 | 「王五」→「相关同事」 |

实测结果：

```
'张三负责的支付重构'  → '相关同事负责的支付重构'   ✓
'与王五合作的项目'    → '与相关同事合作的项目'       ✓
'优化支付链路的稳定性' → '优化支付链路的稳定性'        ✓ 无误伤
'完成季度述职'        → '完成季度述职'              ✓ 无误伤
'排查线上故障'        → '排查线上故障'              ✓ 无误伤
```

### 3.4 模型不产出考核数字

前端契约 `AiPlanItem` 里有 `performanceWeight` / `estimatedValue`，
但**模型不产出这两个值**：

- `performanceWeight` 决定考核权重 —— 属于**考核策略**，必须由业务规则/主管定；
- `estimatedValue` 是业务预估 —— 和指标值一样是**业务层的事实**。

所以模型只产出文字类字段，后端固定权重和预估价值为 0。
输出契约里**物理上没有**这两个字段（`PlanItemOutput` 不含它们）。

### 3.5 只提示不阻断

用户选了「AI 语义判重，只提示不阻断」。所以：

- 体检结论只是 `AiCheckIssue`，`level` 最高是 `WARNING`（不用 `HIGH` 吓人）；
- 业务层**不会**因为有 `duplicate` 就不让员工提交计划。

**计划是员工的权利，AI 的判断可以参考，但不该有否决权。**

### 3.6 默认关闭

计划会变成**考核指标**（计划 → 指标 → 考核）。真模型输出必须由人工确认后才落库，
所以默认 `PLAN_AI_ENABLED=false`。

关闭时端点仍返回**结构完整的空结果** —— 前端不崩、不白屏。

---

## 四、文件清单

### 新增

| 文件 | 作用 |
|---|---|
| `backend/app/services/plan_tool_service.py` | 计划历史读取 + 人名脱敏 |
| `backend/app/routes/plan_tools.py` | L2 只读工具网关（2 个端点） |
| `backend/app/services/plan_ai_service.py` | 业务层：签发令牌 / 调智能体 / 字段收敛 |
| `agent-service/app/plan_agent.py` | 计划助手智能体（generate/adjust/check） |
| `agent-service/app/plan.py` | 工具注册 + 端点编排 |
| `backend/app/tests/test_plan_tools.py` | 工具层测试（18 个） |
| `backend/app/tests/test_plan_ai_endpoints.py` | 端点测试（17 个） |
| `agent-service/tests/test_plan_agent.py` | 智能体测试（18 个） |

### 修改

| 文件 | 改了什么 |
|---|---|
| `backend/app/routes/employee_ai.py` | 8 个 `_empty_*` 换成真实现 |
| `backend/app/core/config.py` | 新增 4 个开关 |
| `backend/app/main.py` | 注册 `plan_tools_router` |
| `backend/app/services/review_service.py` | `AgentClient.plan()`（async） |
| `agent-service/app/base.py` | `EchoModelClient` 支持 plan 形状 |
| `agent-service/app/tool_agent.py` | `content_field` 钩子（不再硬编码 `answer`） |
| `agent-service/app/tools.py` | `HttpToolHandler` 加 `prefix` 参数 |
| `agent-service/app/main.py` | 新增 `/v1/agent/plan` 端点 |

---

## 五、配置项

```env
# backend/.env
PLAN_AI_ENABLED=false                      # 总开关（默认关）
PLAN_AI_ALLOWED_TOOLS=get_my_plan_history,get_department_plan_history
PLAN_AI_HISTORY_MONTHS=6                   # 回看多少个月
PLAN_AI_DEPARTMENT_CONTEXT=true            # 是否参考部门历史
```

agent-service 侧不需要新增配置 —— 复用现有的 `AGENT_TOOL_BASE_URL` 与
`AGENT_SHARED_SECRET`。

---

## 六、测试覆盖

| 套件 | 数量 | 覆盖要点 |
|---|---|---|
| `test_plan_tools.py` | 18 | 越权不可能 / 部门脱敏 / 只读保证 / 审计留痕 |
| `test_plan_ai_endpoints.py` | 17 | 开关默认关 / 契约完整 / 字段收敛 / 降级 / GBK 解码 |
| `test_plan_agent.py` | 18 | 输出契约无考核数字 / 降级 / 工具白名单 / 分数泄漏清洗 |
| agent-service 全量 | 149 | 原有 6 个智能体未受影响 |

---

## 七、遗留与后续

### 7.1 已知的语义局限

**人名脱敏是启发式的**，中文人名识别在理论上无法做到零误伤零漏报。
当前策略是「宁可漏，不可坏」—— 宁可少替换几个真名，也不损坏业务文本。

真正的解法是**结构化**：如果计划条目里能单独存「协作人」字段，
就不需要从自由文本里猜人名。这是数据模型层面的改进，不在 Step 6 范围。

### 7.2 未做的

- `get_plan_vs_metric_alignment`（计划 ↔ 指标对齐检查）—— 属于批次 D 的扩展
- `get_execution_rate` —— 属于批次 B
- 计划草稿的**版本管理**（`draftVersionId` / `draftVersionNo` 目前固定 0）

### 7.3 上线前必须做

1. ~~**真模型压测** —— 和 P3-2 一样，用真实 DB 数据跑多轮检测六类缺陷。~~
   ✅ **2026-11-20 已做单轮真机验证**（见 §八）——
   但**计划场景的多轮压测**仍未做，且判定口径需为计划场景单独定义。
2. **前端联调** —— `PlanAiAssistant.vue` 的草稿展示、采纳流程。⚠️ **未做**
3. **开关灰度** —— 先开 `PLAN_AI_ENABLED=true` + `MODEL_MODE=echo` 验证链路，
   再切真模型小范围灰度。⚠️ **当前配置已是真模型 + 全量开启**，
   上生产前需要先做小范围灰度。

---

## 八、真机验证（2026-11-20）

### 8.1 验证链路

在**真实库** + **真模型**（`deepseek-flash`）上跑通完整闭环：

```
backend /v1/employee/ai/*  →  签发工具令牌
    →  agent-service /v1/agent/plan  →  模型自主调工具
        →  backend /v1/plan-tools/*  →  真实历史计划
    →  清洗（去分数泄漏）→  返回草稿
```

工具调用实测：`get_my_plan_history ok=True 109ms`、
`get_department_plan_history ok=True 109ms`，`degraded=False`。

### 8.2 ⚠️ 验证中发现并修掉的真实 bug：历史计划**永远查不到**

| 项 | 内容 |
|---|---|
| **症状** | 工具恒返回 `available: false, planCount: 0` +「该时间范围内没有历史计划」。模型据此回答「未查到历史计划」→ **参考材料静默失效** |
| **根因** | 库内 `plan_month` 格式是 `YYYY-MM`（如 `2026-11`），但 `_month_range()` 返回 `YYYYMM`（`202606`~`202611`），拿它做**字符串比较**：`"2026-11" >= "202606"` → `False`（`-` 的 ASCII 45 < `0` 的 48）|
| **帮凶** | 同文件的 `_parse_month()` 本该做归一化，但**从未被调用** —— 死函数 |
| **为什么单测没发现** | 测试造数用紧凑格式 `"202610"`，与生产库真实格式不一致 —— **两边错得一样**，断言照过 |
| **修法** | `_month_range()` 产出库内格式 `YYYY-MM`；删死函数；测试数据改真实格式 + 新增 2 条回归用例 |

详见 commit `14dad88`。

### 8.3 顺带修掉一个会随环境漂移的测试

`test_default_is_disabled` 原来直接 `Settings()` → 会读 `backend/.env`。
生产启用 `PLAN_AI_ENABLED=true` 后，「代码默认值是 False」这条断言就红了。
改成 `_env_file=None` + 显式补必填项，断言的才是**代码默认值**。

### 8.4 真模型输出的红线检查

真模型返回 3~5 条计划条目 + 3~4 条体检结论，逐条核对：

| 红线 | 结果 |
|---|---|
| 模型产出考核数字（`performanceWeight` / `estimatedValue` / `score`）| ✅ **无** |
| 分数泄漏（评语里出现绩效分）| ✅ 无（`sanitize.py` 兜住）|
| 编造历史计划（工具没返回却说有）| ✅ 无 —— 如实说「我的历史计划仅返回 1 条草稿记录且无任何条目」|
| 编造引用 ID | ✅ 无 |

### 8.5 ⚠️ 尚未验证的

- **前端联调** —— `PlanAiAssistant.vue` 的草稿展示、采纳流程**未做**
- **计划场景的幻觉压测** —— 计划助手与总结助手输出形状不同
  （`items`/`findings` vs `answer`），P3-2 那套六类缺陷检测器**不能直接套用**，
  需要为计划场景单独定义判定口径
- **多轮非确定性** —— 本次只跑了单轮；模型非确定性问题在总结场景已验证过，
  计划场景未做多轮

---

## 九、一句话总结

Step 6 让计划助手从「点了没反应」变成「真的能查历史、生成草稿、逐条体检」——
关键是**给了它自己查数据的能力**（2 个只读 L2 工具），
同时用「模型不产考核数字 + 只提示不阻断 + 默认关闭」守住红线。

真机验证还额外揪出一个**静默失效**的取数 bug ——
这类「工具返回空、模型照实说没查到」的失败，
**单测全绿也发现不了**，只有真跑真库才暴露。
