# 文档 + 代码修正清单

> 生成时间：2026-11-11 16:37
> 基于：用户代码审查反馈
> 优先级：P0（必须） > P1（重要） > P2（可选）

---

## 一、P0：必须先修的 6 处（阻塞开工）

### P0-1️⃣ 问答场景拆分：progress vs result

**问题**：02:80 的 `self_score` 混淆了"月中进度查询"和"月末结果查询"

**症状**：
- 员工月中问"我这月完成多少"，查 Score 表空（分数月末才产生）
- 文档混成了一个返回字段，实际是两种查询

**修正位置**：`docs/02-问答功能设计.md`

**修改范围**：
- [ ] 第四节（API 设计）4.1 GET /api/qa/query 里的 `query_type` 枚举
- [ ] 响应示例 ① 拆成两部分：
  - 新增 `progress` 类型（查 MetricValue 的滚动达成率）
  - 保留 `result` 类型（月末查 Score + confirmed_rating）

**具体改法**：

```markdown
### 查询类型枚举（修改）

原来：
self_score              查自己的月度分数

改成：
self_progress           查自己本月的滚动进度（MetricValue）
self_result             查自己的月度最终结果（Score）
colleague_progress      查下属的滚动进度
colleague_result        查下属的最终结果
department_progress_summary    查部门的滚动进度汇总
department_result_summary      查部门的最终结果汇总
...其他类型
```

**响应示例 ① 增加（progress）**：

```json
{
  "type": "self_progress",
  "period": "2026-10",
  "as_of": "2026-10-15",         // 新增：数据截至日
  "employee": {...},
  "progress": {
    "metrics": [
      { "metric": "GMV", "value": 1100000, "weight": 0.4, "status": "tracking" },
      { "metric": "退款率", "value": 0.09, "weight": 0.3, "status": "tracking" },
      { "metric": "满意度", "value": 4.2, "weight": 0.3, "status": "not_available" }
    ],
    "completed_weight": 0.7,     // 新增：已出数权重
    "missing_weight": 0.3,        // 新增：缺数权重
    "note": "满意度预计 10 月 20 日出数"
  }
}
```

**响应示例 ③（改为 result 类型）**：

```json
{
  "type": "self_result",
  "period": "2026-10",
  "status": "finalized",         // new: draft / confirmed / finalized
  "employee": {...},
  "score": {
    "total": 78.5,
    "rating": "B",
    "components": [...]
  },
  "confirmed_by": "主管名字",     // new: 谁确认的
  "confirmed_at": "2026-10-31T18:00:00Z"
}
```

**影响范围**：
- [ ] 02 第四节 4.1 查询类型列表
- [ ] 02 第四节 4.1 响应示例 ①（新增 progress）
- [ ] 02 第四节 4.1 响应示例 ③（改名 → result）
- [ ] 02 第五节 5.2 AI 层输入数据要区分

**工作量**：1-2 小时

---

### P0-2️⃣ REST 规范：GET 不能带 JSON body

**问题**：02:125-139 的 `GET /api/qa/query` 带 JSON body，部分网关会丢弃

**修正位置**：`docs/02-问答功能设计.md`

**修改范围**：
- [ ] 第四节 4.2 `GET /api/qa/query` 改成 `POST /api/qa/query`

**具体改法**：

```markdown
### 4.2 POST /api/qa/query（改）

**职责**：返回结构化数据，不调 AI

**请求**：

```json
{
  "type": "self_progress",
  "period": "2026-10",
  "query_params": {
    "employee_id": "..."
  }
}
```

**工作量**：0.5 小时（全文替换）

**附加说明**：也可以改成 `GET /api/qa/query?type=self_progress&period=2026-10` 的 query string 形式，但 POST 更灵活。两选一。

---

### P0-3️⃣ 权限模型：模型输出不可信

**问题**：02:252-258 的流程让模型生成的 `query_type` 和 `target_id` 直接用于权限决策，存在越权风险

**症状**：员工问"查一下张三的分数"，模型可能映射到 `colleague_score` + `target_id=E123`，但 E123 并非下属

**修正位置**：`docs/02-问答功能设计.md`

**修改范围**：
- [ ] 第四节 4.2（API 流程）重写
- [ ] 第五节 5.1（AI 职责边界）补充说明

**具体改法**：

```markdown
### 4.2 POST /api/qa/ask 流程（修改）

原来的流程：
1. 权限检查 + 身份识别
2. 预处理：NLU 解析问题 → 转换为 query_type
3. 调用 GET /api/qa/query 获取结构化数据
4. 调用 AI，返回回答

改成：

1. 权限检查 + 身份识别（从 HTTP Header）
   user = get_current_user(token)  // user.id / user.role / user.department

2. 模型解析：NLU 映射问题到白名单
   query_type_enum = model.map_to_enum(question)
   // 输出只能是：self_result | colleague_result | department_result | trend | ...
   // 如果问题模糊，模型返回 null，系统追问

3. 权限检查 + 数据范围推导（L2 职责）
   if query_type == "colleague_result":
       // 不信模型给的 target_id，从身份推导
       target_ids = db.get_direct_reports(user.id)  // 只能查下属
       if "张三" in question:
           target_id = fuzzy_match_employee(question, candidate=target_ids)
           if not target_id:
               raise PermissionError("张三 不是你的下属")
   elif query_type == "department_result":
       # 检查用户是否有部门权限
       if user.role != MANAGER and user.role != HR:
           raise PermissionError("Only managers/HR can query department")
       department_id = user.department  # 主管只能查自己部门

4. 调用 GET /api/qa/query（已校验，数据安全）
   data = query_service.query(query_type, target_id/department_id)

5. 调用 AI，返回回答
   answer = ai_agent.qa(question, data)
```

**附加约束（新增在第二节或第五节）**：

```markdown
### 关键安全原则

**原则 ① ：模型输出不可信**
- 模型生成的 query_type 只用于查白名单，不能直接用
- 模型生成的 employee_id / department_id 绝不信任
- 所有数据访问权限由 L2 从 HTTP 身份信息推导

**原则 ② ：模型的职责边界**
- ✅ 可以做：理解自然语言，映射到枚举值
- ✅ 可以做：给出"请问你是在查 X 部门还是 Y 部门"这样的澄清
- ❌ 不能做：查数据库，返回 ID，做权限决策

**原则 ③ ：追问优于假设**
- 如果问题模糊（比如"查部门平均分"但用户有多个部门）
- 模型应该返回 null + "请问你是在查 A 部门还是 B 部门？"
- 不应该猜一个 department_id
```

**工作量**：2 小时

---

### P0-4️⃣ 隐私口径自相矛盾

**问题**：03:458 说"AI 看不到姓名"，但 02:158 示例带 `"name": "张三"`；填报辅导必须读自评文本

**修正位置**：`docs/02-问答功能设计.md` 第五节 + `docs/03-操作手册.md` 第七节

**修改范围**：
- [ ] 02 第五节 5.2 AI 层输入数据 —— 按智能体分别定义
- [ ] 03 第七节 Q7 隐私相关 —— 改成更准确的说法

**具体改法**：

```markdown
### 5.2 输入数据（按智能体）

原来（笼统）：脱敏后的数据

改成（分别）：

#### 问答智能体（QA）—— 数据最小化
```python
@dataclass
class QAInput:
    question: str              # 用户的问题
    query_result: dict         # 结构化数据
    # 包含：user_id (alias), score, components
    # 不包含：name, department, other PII
    
    user_role: str             # "employee" | "manager" | "hr"
    # 用于理解权限上下文（但仍为代码/角色，不是姓名）
```

#### 填报辅导智能体（FillCoach）—— 需要自评文本
```python
@dataclass
class FillCoachInput:
    employee_id: str           # 代码，不是姓名
    self_review_text: str      # 必须有，才能理解和追问
    form_schema: dict          # 表单定义
    
    # 审计信息（留痕）
    trace_id: str              # 审计 ID
    timestamp: str             # 何时脱敏
    deidentified_by: str       # 谁脱敏的
```

#### 证据校验智能体（Evidence）—— 需要指标值
```python
@dataclass
class EvidenceInput:
    employee_id: str
    metric_values: List[{
        "metric_code": "gmv",
        "value": 1250000,
        "source": "api"
    }]
    evidence_items: List[...]  # 支撑证据（代码化）
```

```

**在 03 里改（第七节 Q7）**：

```markdown
### Q：我的隐私数据会被 AI 看到吗？

**按场景分**：

**① 填报辅导（追问时）**
- AI 能看到：你的自评文本
- AI 看不到：你的姓名、联系方式、照片
- 目的：理解你写的是否完整、数字是否合理
- 保护：脱敏后留痕，后台有审计日志

**② 证据校验（主管打分前）**
- AI 能看到：指标数字、考勤异常数量
- AI 看不到：具体的自评内容、个人信息
- 目的：发现明显异常（比如考勤记录不一致）
- 保护：都是代码化数据，无 PII

**③ 问答（查分时）**
- AI 能看到：你的分数、各项成分
- AI 看不到：你的姓名、其他人的分数
- 目的：用自然语言解释你的分数
- 保护：只有权限范围内的数据暴露

总结：AI 看到的都是"要看才能工作"的数据。不需要看的数据（姓名、照片、私人备注）一律不给。
```

**工作量**：1.5 小时

---

### P0-5️⃣ 置信度指标改法

**问题**：02:271 的 `confidence: 0.95` 由模型自报，没有意义

**修正位置**：`docs/02-问答功能设计.md` 第四节 4.2

**修改范围**：
- [ ] 响应格式（POST /api/qa/ask）的 `confidence` 字段改成 `data_quality`

**具体改法**：

```markdown
### 响应（改）

原来：
```json
{
  "answer": "你本月绩效分数是 78.5...",
  "data_source": {...},
  "confidence": 0.95,  // ❌ 模型自报，没用
  "follow_up_questions": [...]
}
```

改成：
```json
{
  "answer": "你本月绩效分数是 78.5...",
  "data_quality": {
    "completeness": 0.95,       // (已出数权重 / 总权重)
    "as_of": "2026-10-31",
    "degraded": false,          // AI 是否降级使用模板
    "citations": [              // 数据来源
      "Score.total",
      "MetricValue.gmv",
      "MetricValue.feedback_rate"
    ]
  },
  "follow_up_questions": [
    "我哪项指标最差",
    "这月 vs 上月趋势"
  ]
}
```

说明：
- `completeness` 由 L2 计算（所有指标都有数据 = 1.0，缺 30% = 0.7）
- `as_of` 由 L2 给出（数据最新到何时）
- `degraded` 由系统给出（AI 是否降级）
- `citations` 由 AI 补充，但由 L2 校验（不能引用无权限数据）
```

**工作量**：1 小时

---

### P0-6️⃣ agent-service 测试失败修复

**问题**：`test_grounding_downgrades_when_no_evidence` 失败，gaps 为空

**位置**：`framework/agent-service/tests/test_draft_api.py:136`

**根本原因**：gaps 的定义逻辑只检查 `if not m.evidence_ids`（列表是否为空），但没有检查 evidence_ids 指向的证据是否真实存在

**修正内容**（已实现）：在 `main.py:204-233` 增加两层检查：
1. metric 没有 evidence_ids 列表 → gap
2. metric 的 evidence_ids 指向的证据 ID 在 evidence 表里不存在 → gap

**验证结果**：✅ 
- [x] test_grounding_downgrades_when_no_evidence → PASSED
- [x] agent-service 全部 7 个测试 → PASSED  
- [x] backend 全部 22 个测试 → PASSED
- [x] **合计：29 个测试全绿**

**工作量**：1 小时（已完成）

**改动文件**：
- [x] `agent-service/app/main.py` (L204-233)

---

## 二、P1：文档与代码对不上（修订 + 标注）

### 表格：对应关系修正

| 文档说的 | 实际代码 | 修正方式 | 优先级 |
|---|---|---|---|
| `app/routes/tasks.py` 等（00:41,48） | `backend/app/main.py` 一个文件里的路由 | 改 00 的目录树，标注"实际路由都在 main.py" | P1-中 |
| `models.py`, `privacy.py`, `model_client.py`（00:63-66） | 不存在；实际是 `base.py`, `grounding.py`, `perf_rules.py` | 改 00 的目录树 + 第三节 | P1-中 |
| `core/database.py`（00:30） | 实际是 `core/db.py` | 改 00 目录树 | P1-低 |
| `scripts/db_init.py`, `tests/conftest.py`（00:51,55） | 不存在 | 删掉这两行，改成"初始化见 README 快速开始" | P1-低 |
| `Score.rating_level`（00:305） | 不存在；等级在 `AssessmentRecord.confirmed_rating` | 改 00 第四节的数据模型说明 | P1-中 |
| `Score.history` 表（02:452） + `ScoreHistory` 表（00:97） | 两处命名不一致，且都还没建 | 统一命名为 `ScoreHistory`，标注"待建" | P1-中 |
| `BaseAgent.run(input_data)` 五步管线示意代码（00:151-183） | 实际签名是 `run(**kwargs) + build_payload()`；示意图不准 | 标注"示意代码，实际见源码" | P1-低 |
| `hash(input_data)`（00:178） | dict 不可哈希，TypeError | 改成 `json.dumps(input_data, sort_keys=True)` | P1-中 |
| `sum(v.value * weight)`（00:298） | 量纲错误；应先按 rubric 归一成得分，再加权 | 改成"1. 取原始值 2. 按 rubric 查表转换成得分 3. 加权求和" | P1-中 |

### 修正清单

- [ ] **修改 `00-框架实现指南.md`**
  - [ ] 第一节开头加段落："本文档结构分为设计目标（第二-四节）和现状代码（第五-九节）。目录树、类名、字段名等以代码为准，见 README。伪代码用 `// 示意` 标注。"
  - [ ] 第二节目录树改成实际结构
    - `app/routes/` → 删掉，改成"路由定义在 `backend/app/main.py`"
    - `app/domain/` → 保留
    - `app/services/` → 保留
    - `agent-service/app/models.py` → 删掉
    - `agent-service/app/utils/privacy.py` → 删掉
    - `agent-service/app/utils/model_client.py` → 删掉
    - `backend/core/database.py` → 改成 `core/db.py`
    - `backend/scripts/db_init.py` → 删掉
    - `backend/tests/conftest.py` → 删掉
  - [ ] 第四节的数据模型说明改成：
    ```
    Score: id, record_id, total, per_metric_json, confirmed_at, trace
    （不含 rating_level；等级在 AssessmentRecord.confirmed_rating）
    ```
  - [ ] 第三节的代码示意：
    - hash(input_data) 改成 `json.dumps(input_data, sort_keys=True)`
  - [ ] 第四节的 scoring 代码：
    ```python
    # 改法
    for v in scoring_values:
        score_for_metric = lookup_score_in_rubric(v.value)  # 查表转换
        weighted_contribution = score_for_metric * weights[v.metric_code]
        total += weighted_contribution
    ```

- [ ] **修改 `02-问答功能设计.md`**
  - [ ] 第八节实现清单里加：
    ```
    - [ ] 5. 修改 agent-service/app/agents.py
      - [ ] 新增 QA 类的 validate_schema()
      - [ ] 返回包含 data_quality（不是 confidence）
    ```

- [ ] **修改 `00-框架实现指南.md` 第六节常见问题**
  - [ ] Q1 增加："查 `Score.trace` 字段（包含完整的计算过程和权重）"
  - [ ] Q2 增加对 `per_metric_json` 的说明

**工作量**：3 小时（逐项检查 + 改文）

---

## 三、P2：设计建议调整（方案优化）

### P2-1 小部门聚合 k-匿名

**问题**：部门只有 2-3 人时，平均分能反推出个人

**决策**：少于 5 人的部门汇总不显示平均值

**修改位置**：`docs/02-问答功能设计.md` 第四节

**改法**：

```markdown
### 响应示例 ②（部门汇总）添加约束

"summary": {
    "total_employees": 3,
    "scored_employees": 3,
    "average_score": null,         // ← 新增：< 5 人时为 null
    "note": "部门员工数 < 5，隐私保护中不显示平均分",
    ...
}
```

**代码约束**：在 `qa_service.py` 里加：

```python
def query_department_summary(...):
    if total_employees < 5:
        result["average_score"] = None
        result["note"] = "样本过小，隐私保护中"
    return result
```

**工作量**：0.5 小时

---

### P2-2 排名查询先别做

**问题**：02:28 的"本月谁表现最好/最差"敏感排名容易被挪作他用

**决策**：第一期删掉这个查询类型，只返回原始指标

**修改位置**：`docs/02-问答功能设计.md` 第四节

**改法**：

```markdown
### 查询类型枚举（删除）

移除这两条：
- low_performers    ❌ 删掉
- high_performers   ❌ 删掉

改成：
- peer_raw_metrics  ✅ 保留：给主管看原始指标，但不排名
```

**工作量**：0.5 小时

---

### P2-3 加密方案不能说"不需要"

**问题**：02 最后说"HTTPS + 权限检查，不需要额外加密"太草率

**修改位置**：`docs/02-问答功能设计.md` 第九节 D4

**改法**：

```markdown
### D4：敏感字段的加密（修改）

原来：
HTTPS + 权限检查，不需要额外加密

改成：
数据敏感性分级（待 IT / 法务确认）：

1. 传输层：HTTPS（强制）
2. 存储层：
   - 数据库加密（待确认：全表 or 仅分数字段？）
   - 备份加密（待确认）
   - 日志加密（待确认）
3. 应用层：
   - 审计日志（谁查了谁的数据，何时）
   - 数据保留期限（待确认：多长时间后删除？）
4. 访问控制：
   - 权限检查（已实现）
   - 行级安全（SQL 触发）
   - 日志审计（何人何时何操作）

具体要求需要 IT 和法务评审后补充。第一期先实现 1 + 2.1 + 3.1，其他待确认。
```

**工作量**：0.5 小时（等 IT / 法务反馈）

---

### P2-4 删掉缓存设计

**问题**：缓存 D1 是过度设计，100 人聚合毫秒级，不需要缓存

**修改位置**：`docs/02-问答功能设计.md` 第九节

**改法**：

```markdown
### 关键设计决策

D1：~~查询结果缓存~~ ❌ 删掉

理由：
- 100 人规模的聚合查询 < 100ms
- 加缓存只增加"为什么答案没变"的解释成本
- 等实测瓶颈再加，现在不做

D2：跨月对比的数据源（保留）
...

D3：模型不可用时（保留）
...

D4：敏感字段的加密（见上，改法）
```

**工作量**：0.5 小时

---

### P2-5 鉴权流程定义（需 IT / 人事部确认）

**问题**：get_current_user 用自签 JWT，但系统入口是飞书免登，流程不清

**修改位置**：`docs/02-问答功能设计.md` 第九节 新增 D5 + `docs/00-框架实现指南.md` 第六节

**待确认的问题**：

1. 飞书 OAuth 流程是什么？
   - 用户从飞书 app 进入？还是 web 链接？
   - code 怎么换 access_token？
   - employee_id 怎么从通讯录 ID 映射？

2. Token 有效期多长？
   - 短期 session token（比如 1 小时） + refresh token
   - 还是直接用飞书的 access_token（有效期通常是 2 小时）？

3. 过期 / 刷新逻辑？
   - 前端怎么处理 401？
   - 后端怎么区分"token 过期"和"无权限"？

**改法（等确认后）**：

```markdown
### D5：身份验证与授权流程

当前问题：
- 如何从飞书获取 employee_id？
- Token 的有效期和刷新机制？
- 401 vs 403 怎么区分？

等待输入（人事部 / IT）：
- [ ] 飞书应用的 OAuth 配置
- [ ] 通讯录 ID ↔ 员工 ID 的映射规则
- [ ] Token 管理策略

临时方案（可用但不合规）：
- 后台用自签 JWT + HS256
- Employee_id 硬写在 token 的 sub 里
- 24 小时过期，无 refresh

最终方案（合规）：
- 飞书 OAuth → access_token
- 后台用 access_token 查通讯录 → employee_id
- 后台签发 short-lived session token（15 分钟）+ refresh token（7 天）
- 前端自动刷新 session token
```

**工作量**：等待 IT 反馈（现在是 TODO）

---

### P2-6 文档重复问题

**问题**：README、01/02/03、框架设计正文大量重叠，容易漂移

**修改位置**：所有文档

**改法**：定单一事实源

```
README.md
  ├─ 快速开始（跑起来）
  ├─ 项目结构（目录树）
  └─ 常见命令（pytest / linter / 部署）

docs/01-架构与决策.md
  ├─ 项目本质定义
  ├─ 五层架构为什么这样分
  ├─ 四条不可破原则
  └─ 九项已定决策
  （不重复解释"怎么跑"）

docs/02-问答功能设计.md
  ├─ 功能定义（为什么需要）
  ├─ 接口规格（请求/响应）
  └─ 权限矩阵
  （不讲整体架构）

docs/03-操作手册.md
  ├─ 员工流程
  ├─ 主管流程
  └─ HR 流程
  （不讲技术实现）

docs/00-框架实现指南.md
  ├─ 现状代码结构
  ├─ 如何添加功能
  └─ 常见问题排查
  （实操手册）

代码注释
  ├─ 类级文档字符串
  ├─ 方法级文档字符串
  └─ 复杂逻辑的行注释
```

**改法具体步骤**：

- [ ] README 只保留"快速开始" + "目录"，删掉架构解释
- [ ] 01 补充"与现有文档的关系"一节
- [ ] 02 删掉与整体架构重叠的部分
- [ ] 03 完全独立（只讲操作）
- [ ] 00 标注"现状代码对应关系"

**工作量**：2 小时（各文档各剪一遍）

---

## 四、排期重新定义

### 当前问题

原排期："D1 发起考核..."

实际依赖：
- 指标定义表（由人事部提） ← 卡点
- 试点部门名单（由人事部定） ← 卡点  
- 现行制度原文（由人事部给） ← 卡点

### 改法

新排期定义：

```markdown
### 排期定义（修改）

**D0（第 0 天）：前置条件到位**

人事部需要提供：
- [ ] 指标定义表（CSV/Excel，标准格式）
- [ ] 试点部门名单（包括部门 ID、负责人）
- [ ] 现行绩效制度文档（原文）
- [ ] 飞书应用权限确认（IT）
- [ ] 数据合规意见（IT / 法务）

**D1-20：开发工作（以上 D0 到位为起点）**

前期：D1-6   （P0：主管评分 + 单人闭环）
中期：D7-14  （P1：飞书集成 + 多部门）
后期：D15-20 （P2：Q&A + 申诉 + 导出）

**D21+：后续功能（单独排期）**
- H5 完整动态表单（另排 1-2 周）
- PC 后台前端（另排 1-2 周）
- 性能优化 / 兼容性测试（另排 1 周）
```

### 工作量重估

| 块 | 估算 | 备注 |
|---|---|---|
| D0：指标表导入 + 任务配置 | 3pd | 取决于人事部提供的格式 |
| 主管评分 + 部门复核 + 状态机 | 3-4pd | 现有骨架 OK，主要是权限逻辑 |
| 飞书（权限、通讯录、三套审批、回调+轮询） | 5-7pd | 风险最高，取决于飞书 API 稳定性 |
| 汇总 + 导出 | 3-4pd | 聚合查询标准操作 |
| 申诉 | 2-3pd | 独立的状态机 + 邮件通知 |
| H5 证据页 + 完整动态表单 | 5-7pd | 前端复杂度高，分离到 P3 |
| 问答（Q&A）| 3pd | 已有框架，主要是权限矩阵 |
| PC 后台前端 | 5-8pd | 分离到 P4 |
| **合计** | **29-39pd** | 2 人全职 = 14-20 天 |

**现实排期**：
- 2 人全职 + D0 到位 → 20 天能做到 P2（可试用）
- 2 人全职 + D0 到位 → 30 天能做到 P3（几乎完整）
- 1 人全职 → 40-50 天才能到 P3

### 修改位置

- [ ] `docs/01-架构与决策.md` 第九节（时间假设与风险）
  - [ ] 改 D0 定义，加"人事部提供清单"
  - [ ] 改排期假设表
  - [ ] 改"关键路径上三个不等人"为"五个"（加飞书权限 + 合规意见）

- [ ] 新建 `SCHEDULE.md`（详细排期表）
  - 按周列出 P0 / P1 / P2 / P3 / P4 的关键交付

**工作量**：1 小时

---

## 五、总体优先级和行动顺序

```
P0（今天/明天做）
├─ 📌 P0-6：修 agent-service 测试（我现在诊断）
├─ 📝 P0-1 ~ P0-5：改 5 份文档（2-3 小时）
└─ ✅ 合并成"修正版 P0"，所有测试绿

P1（本周做）
├─ 📝 改 00 的目录树和代码示意（3 小时）
└─ 📝 改 02 的数据模型说明（1 小时）

P2（下周或等输入）
├─ 📝 加 k-匿名 + 删排名（1 小时）
├─ 📝 改加密方案为"待确认"（0.5 小时）
├─ 📝 删缓存设计（0.5 小时）
├─ ⏳ 等 IT/人事部 确认：飞书 OAuth + 合规（无法动作，等输入）
└─ 📝 改排期定义（1 小时）

文档整理
└─ 📝 定单一事实源（2 小时，等 P1 完成后）
```

---

## 六、清单使用说明

### 按类型查找

- **要改文档**：搜 `📝` + 文件名
- **要改代码**：搜 `🔧` （暂无，都是文档改）
- **要诊断**：搜 `🔍` （P0-6）
- **要等输入**：搜 `⏳` （P2-5）

### 按优先级查找

- **今天动手**：P0-1 ~ P0-6
- **本周完成**：P1-1 ~ P1-8
- **有时间再做**：P2-1 ~ P2-6
- **完全依赖外部**：排期重定义

### 验收标准

| 阶段 | 验收标准 |
|---|---|
| P0 完 | ✅ agent-service 32 个测试全绿；02 文档四处场景都覆盖 |
| P1 完 | ✅ 00/01/02 和代码对应无误差；新人找文件能找对 |
| P2 完 | ✅ 所有 ⚠️ 变成 ✅；排期写进 README |

---

## 七、这个清单的管理

### 打开方式

```bash
# 在 Cursor 或 VS Code 打开这个文件
# 用 Ctrl+F 搜索：P0-1 / P1-1 / P2-1 等快速定位

# 或打开后按 Markdown Outline 查看层级
```

### 更新方式

每完成一项，改成：
```markdown
- [x] 项目标题  ✅ 完成于 HH:MM
```

### 依赖追踪

如果 P0-3 需要 P0-2 先完成，在 P0-3 下面加：
```
**前置**：P0-2 ✅ (已完成)
```

---

**现在就可以开工。要不要我先修 P0-6（诊断测试）？**
