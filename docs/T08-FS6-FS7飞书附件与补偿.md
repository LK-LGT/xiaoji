# FS-6 / FS-7 实现说明（2026-11-24）

> 落地「绩效评定表飞书推送」的 Excel/PDF 附件，与「飞书 token 缓存 + 审批定时补偿」。
> 遵循 `docs/01-架构与决策.md` 的四条铁律与 `docs/14-工具层设计方案.md` 的边界。

---

## 一、总览

| 项 | 交付物 | 测试 |
|---|---|---|
| **FS-6** 评定表附件 | `feishu_rating_export.py`（生成）+ `feishu_client.upload_file/send_file_message`（投递） | 11 例 |
| **FS-7** token 缓存 | `feishu_token_cache.py`（进程级共享缓存） | 14 例 |
| **FS-7** 审批定时补偿 | `feishu_approval_compensation.py`（补偿逻辑）+ `feishu_approval_compensation_scheduler.py`（调度器） | 12 例 |

合计 **37 例新增测试**，全部通过；后端全量回归无失败。

---

## 二、FS-6 · 绩效评定表附件

### 2.1 问题

N8「绩效等级已发布」原本只有一句话：
「你的绩效结果已确认，请登录系统查看分数和等级」。

员工在飞书收到通知后，**得点进系统 → 找到对应考核 → 翻到那一页**才能看到分数。
主管核对下属得分时更麻烦 —— 得一个个登录去查。

### 2.2 数据流

```
N8 通知（notification_service.dispatch_pending）
   ├─ 发文字消息（原有行为，不变）
   └─ ★ 额外发一条 file 消息（附件）
          ├─ feishu_rating_export.build_rating_attachment()  → 生成 xlsx
          ├─ client.upload_file(filename, content)            → file_key
          └─ client.send_file_message(receive_id, file_key)   → 投递
```

### 2.3 关键设计决策

**① 附件必须是独立的一条消息**

飞书的消息类型**互斥** —— 卡片（`interactive`）不能带 `file_key`，
文件（`file`）不能带正文。

⇒ 要「一句话 + 一个附件」就**必须发两条消息**。

**② 导出只读，绝不回写**

`feishu_rating_export` 只读 `AssessmentRecord` / `Score` / `MetricValue`，
**不产生任何写入**。

⇒ 符合铁律「模型永不生成或改写分数」——
分数的唯一真值是 `Score` 表，本模块只是**呈现**它。
（测试 `test_export_does_not_write_to_score` 守住这条。）

**③ Excel 不写公式**

贡献分直接写**算好的数值**。写公式的话，Excel 打开时会按公式重算，
一旦口径变了，显示值与系统真值就对不上。

**④ PDF 默认不生成，自动降级**

| 格式 | 依赖 | 处置 |
|---|---|---|
| **xlsx** | `openpyxl`（**已在 `requirements.txt`**） | ✅ 默认 |
| pdf | `reportlab`（**未安装**） | 缺依赖 → 降级 xlsx |

PDF 还有个坑：服务器上中文字体常常缺字（渲染成方块 ⇒ 文档不可读）。
⇒ `_register_chinese_font()` 找不到中文字体时**直接报错**，
而不是生成一份看不懂的 PDF。

**⑤ 附件失败不推翻主消息**

文字已经送到了，附件是「锦上添花」。
⇒ 附件失败只记 `warning`，主消息仍算 `sent`。

### 2.4 Excel 结构

| Sheet | 内容 |
|---|---|
| **概览** | 考核任务 / 周期 / 员工姓名 / 最终等级 / 记录状态 / 总分 / 主管评语 / 自评内容摘要 |
| **指标明细** | 14 列：指标编码、名称、权重、归一权重、实测值、目标值、达成率、档位、档位说明、档位得分、贡献分、单位、数据来源、数据截至 |

列顺序即**阅读顺序** —— 从「是什么」到「算成多少」。

### 2.5 权限

发文件需要飞书应用的 `im:resource` 权限点（上传）与 `im:message:send_as_bot`（发消息）。

---

## 三、FS-7 · 飞书 token 缓存

### 3.1 问题：原缓存形同虚设

`FeishuClient` **内部已有** token 缓存
（`_access_token` + `_token_expires_at` + 提前刷新），
但那是**实例级**的 —— 而项目里每个调用点都是**现建现用**：

```
routes/feishu_bot.py        _client()      每个请求一个实例
routes/feishu_auth.py       _client()      每个请求一个实例
routes/feishu_approval.py   _client()      每个请求一个实例
services/feishu_daily_push.py                每次推送一个实例
services/feishu_metric_sync.py               每次同步一个实例
```

⇒ **实例级缓存等于没有缓存** —— 每个 API 调用都要先打一次
`/auth/v3/tenant_access_token/internal`。

代价不只是慢：飞书对 token 接口有频控，
审批回流 + 组织同步 + 每日推送叠加起来会撞限流 ⇒ 整条链路失败。

### 3.2 实现

新增 `feishu_token_cache.py` —— **进程级**共享缓存：

| 特性 | 说明 |
|---|---|
| **按凭据分槽** | key = `(app_id, app_secret)` 的哈希。不同应用的 token **绝不共享** —— 否则跨应用越权 |
| **不存明文密钥** | key 里是 `app_secret` 的 SHA-256 前 16 位，不是明文 |
| **线程安全** | 后端是多线程（FastAPI 同步端点跑在线程池里） |
| **单飞（single-flight）** | per-key 锁保证同一应用**同时只有一个线程**在拉 token |
| **双检** | 等锁期间可能已被别人刷新好 ⇒ 拿到就返回，不重复拉 |

### 3.3 ⚠️ 测试隔离：注入 transport 时禁用共享缓存

注入 transport 的是**测试用的 `MockTransport`** —— 它按录制顺序返回响应。

若共享缓存生效，token 会跨实例复用 ⇒ A 测试拿到的 token 被 B 测试「继承」
⇒ 断言互相污染，而且**换个测试顺序就会失败**（典型的顺序依赖）。

⇒ `FeishuClient._transport_injected` 标记：注入 transport ⇒ 只用实例级缓存。

### 3.4 ⚠️ 顺带修掉一个真 bug

原代码在收到飞书 token 失效错误码时，只清了**实例**槽位：

```python
self._access_token = None
self._token_expires_at = None
```

共享缓存加入后，这**不够** —— 下一次重试会从共享缓存拿到**同一个已失效的 token**
⇒ 重试再次 99991663 ⇒ 而 `token_refreshed` 已为 True ⇒ 直接抛错
⇒ **一次 token 失效导致整个请求失败**。

⇒ 共享槽位与实例槽位**必须一起作废**。

### 3.5 缓存范围

只缓存**租户态** token。三类 token 语义完全不同，绝不混用（RK3）：

| token 类型 | 缓存策略 |
|---|---|
| `tenant_access_token` | ✅ 本模块缓存（进程级、应用身份） |
| `app_access_token` | ❌ 不缓存（换取 user token，用完即弃） |
| `user_access_token` | ❌ 不缓存（**属于某个用户**，权限随人变） |

---

## 四、FS-7 · 审批定时补偿

### 4.1 问题：webhook 不可靠

审批回流原本**只靠** webhook 事件推送。但：

- 飞书侧偶发丢事件（服务抖动、网络分区）；
- 事件投递失败后**不会补发**；
- 本服务重启期间的事件**永久丢失**。

⇒ 员工在飞书点了「通过」，但本系统的计划**一直卡在审批中**。

### 4.2 实现

`feishu_approval_compensation.py` —— 扫描 `plan_feishu_binding` 表。

**判据（三条同时满足）**：

1. `instance_code` 非空 —— 没有飞书实例就无从拉起；
2. `instance_status` 不是终态（`APPROVED` / `REJECTED` / `CANCELED` / `WITHDRAWN`）;
3. `updated_at` 早于「现在 − 陈旧阈值」。

**⚠️ 为什么用 `updated_at` 而不是 `created_at`**
`updated_at` 带 `onupdate` —— 每次回流更新绑定状态时它会跟着走。
⇒ 一个实例被补偿成功后 `updated_at` 前移 ⇒ 下一轮不再重复拉。
⇒ 这正是「陈旧阈值」能自愈的原因。

**⚠️ 终态过滤放在 Python 侧**
不同飞书审批模板可能有非标准终态字符串，
在 SQL 里写死 `NOT IN (...)` 反而容易漏。

### 4.3 关键设计决策

**① 与 webhook 互补，不是替代**

两条路径最终都走 `apply_feishu_approval_event`，
靠 `dedup_key`（`feishu:{type}:{id}:{action}:{ver}:{instance}`）幂等
⇒ 同一个事件被两条路径各处理一次，也**只会写一条流水**。

**② 单个失败必须隔离**

一个实例失败**不得**中断整轮 —— 否则后面的实例都得不到补偿。
⇒ `try/except` 包住每个实例，失败时 `db.rollback()` 只回滚这一条。

**③ 陈旧阈值 = 在「及时性」与「频控」之间取平衡**

| 参数 | 默认 | 说明 |
|---|---|---|
| `feishu_approval_compensation_interval_seconds` | 600（10 分钟） | 轮询间隔 |
| `feishu_approval_compensation_stale_seconds` | 300（5 分钟） | 陈旧阈值 |
| `feishu_approval_compensation_max_per_run` | 50 | 单轮上限 |

**单轮上限必须有** —— 否则一次性积压几百个待审批实例时，
一轮全拉完会触发飞书频控，且这一轮会跑很久（下一轮又被推迟，形成恶性循环）。

**④ 默认关闭**

补偿会**主动调用**飞书审批接口（消耗频控配额）**并写入业务状态**
⇒ 必须业务显式开启。

### 4.4 运维接口

| 端点 | 用途 |
|---|---|
| `GET /v1/feishu/approval/compensation/status` | 调度器运行态 |
| `POST /v1/feishu/approval/compensation/run` | 手工触发一轮（webhook 漏推时不必等定时器） |

---

## 五、配置项

```ini
# FS-6 —— 评定表附件格式（默认 xlsx）
# ⚠️ 配成 pdf 时，缺 reportlab 依赖或缺中文字体会自动降级为 xlsx
FEISHU_RATING_ATTACHMENT_TYPE=xlsx

# FS-7 —— 审批定时补偿（⚠️ 默认关闭）
FEISHU_APPROVAL_COMPENSATION_ENABLED=false
FEISHU_APPROVAL_COMPENSATION_INTERVAL_SECONDS=600
FEISHU_APPROVAL_COMPENSATION_STALE_SECONDS=300
FEISHU_APPROVAL_COMPENSATION_MAX_PER_RUN=50
```

---

## 六、⚠️ 待真机验证

以下项**单元测试覆盖不了**，需真机（真实飞书应用）验证：

| 项 | 验证要点 |
|---|---|
| `upload_file` | 需飞书应用开启 `im:resource` 权限；未开通会返回 99991672 |
| 附件在客户端可预览 | `file_type` 与扩展名必须匹配，否则上传成功但**打不开** |
| `send_file_message` | 需 `im:message:send_as_bot` |
| 审批补偿 | 需构造一个「飞书已通过、本系统仍审批中」的场景 |
| token 共享缓存 | 需观察飞书开放平台 token 接口的调用频次是否下降 |

---

## 七、兼容性说明

| 变更 | 影响 |
|---|---|
| `FeishuClient._request` / `_request_with_token` 新增 `files` 参数 | **向后兼容**（默认 `None`）。⚠️ `files` 与 `json` 互斥 —— httpx 要求 |
| `FeishuClient._get_access_token` 改走共享缓存 | 注入 transport 的测试**不受影响**（走实例级缓存）；真实调用**行为不变**（token 语义、刷新余量、失效重试全部保留） |
| `notification_service.dispatch_pending` 对 N8 多取一次 `AssessmentRecord` | 仅多一次主键查询，无副作用 |
| 新增 4 个调度器配置项 | 默认值不改变任何现有行为（补偿默认关闭） |
