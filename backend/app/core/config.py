"""应用配置。"""

from __future__ import annotations

from pathlib import Path

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# backend/app/core/config.py -> backend/
BACKEND_ROOT = Path(__file__).resolve().parents[2]

# 已知的弱密钥。生产环境禁止使用。
_WEAK_AUTH_SECRETS = {
    "",
    "change-me-in-production",
    "changeme",
    "secret",
    "your-secret",
    "your-secret-key",
}


class Settings(BaseSettings):
    # 显式指定 .env 路径，避免因启动工作目录不同而读不到配置。
    model_config = SettingsConfigDict(
        env_file=(BACKEND_ROOT / ".env", ".env"),
        extra="ignore",
    )

    # 数据库
    database_url: str = "sqlite:///./data/perf.db"
    # 旧版认证使用固定 Mock Token；启用 secure_auth 后只接受 HMAC Token。
    secure_auth: bool = True

    # Schema 管理：生产由 Alembic 负责；仅本地开发可临时开启自动建表。
    auto_create_tables: bool = False

    # ─────────────────────────────────────────────────────────────
    # ★ 登录入口开关（2026-11-24，业务规则确认）
    #
    # ⚠️ **业务规则**
    # 「登录入口为飞书单点登录，只允许公司内真实存在、组织架构内同步成功的
    #   员工账号进入系统」⇒ 生产环境应当**只走飞书 SSO**。
    #
    # ⚠️ **为什么不是直接删掉密码登录**
    # Runbook §3.2 约束 1：飞书故障时全员无法登录是灾难
    # ⇒ 保留密码入口作为**兜底**，但默认**关闭**，由部署方显式开启。
    #
    # ⚠️ **三种取值的后果**
    #   - ``False``（默认/生产）⇒ ``/v1/auth/login`` 对**口令**返回 503
    #     ⇒ 只能走飞书免登。**测试账号同样被禁**（见 ``test_users_enabled``）。
    #   - ``True`` ⇒ 允许员工用**自己的**口令登录（改密后的强口令）。
    #     ⚠️ 存量弱口令（``123456``）走兼容路径，登录后强制改密。
    password_login_enabled: bool = False
    #
    # ⚠️ **测试账号是否可用**（自动化测试用）
    #   - 源码里**不再有明文口令**（2026-11-24 已移除）⇒ 口令只能来自
    #     环境变量 ``TEST_USER_PASSWORDS``（JSON）。
    #   - ⇒ 没有配这个环境变量 ⇒ 测试账号**一律不存在** ⇒
    #     「源码里 grep 得到密码」这条路彻底堵死。
    #   - ⚠️ 与 ``password_login_enabled`` **独立**：
    #     pytest 里会显式把 ``password_login_enabled`` 打开（测试要登录），
    #     但生产环境**两个都必须是关的**。
    test_users_enabled: bool = False

    # 智能体服务
    agent_service_url: str = "http://127.0.0.1:8800"
    agent_shared_secret: str = ""
    # 留空时工具网关返回 503；不影响既有业务启动。
    agent_tool_secret: str = ""
    agent_tool_token_ttl_seconds: int = 300

    # 模型
    model_name: str = "echo-deterministic"
    model_mode: str = "echo"  # echo | null | openai

    # 计划助手（Step 6 工具层）
    #
    # ⚠️ 为什么默认关闭：
    #   计划助手生成的内容会进入**考核指标链**（计划 → 指标 → 考核）。
    #   真模型输出不可完全信任，必须由人工确认后才会落库；
    #   在压测与契约测试全部通过前，保持关闭，只跑 echo 链路。
    #
    # 关闭时端点仍返回**结构完整的空结果**，前端不崩、不白屏。
    plan_ai_enabled: bool = False
    # 计划助手允许调用的工具（与计划域网关白名单一致）
    plan_ai_allowed_tools: str = "get_my_plan_history,get_department_plan_history"
    # 计划助手回看多少个月的历史计划
    plan_ai_history_months: int = 6
    # 部门级历史计划开关（关闭则只参考自己的历史）
    plan_ai_department_context: bool = True

    # 业务开关
    # 强制分布策略（决策⑦）：第一期只做分布对比，不强制比例
    rating_policy: str = "reference_only"

    # 考勤异常口径（决策②配套）
    attendance_display_types: str = "late,early_leave,missing_punch,outside,leave,overtime"

    # 是否展示"数据截至"标注
    show_as_of: bool = True

    # 应用登录认证密钥。生产环境必须通过环境变量注入，且不得写入日志。
    auth_token_secret: str = "change-me-in-production"
    # 密钥轮换过渡期保留的旧密钥；轮换完成后应清空。
    auth_token_secret_prev: str = ""

    # 飞书集成
    feishu_app_id: str = ""
    feishu_app_secret: str = ""
    feishu_tenant_id: str = ""
    notification_enabled: bool = False
    # 通知灰度（canary）：配置后仅向指定员工/场景/单据投递，
    # 用于上线前验证飞书通道，不影响其他收件人。
    notification_canary_employee_id: str = ""
    notification_canary_scene: str = ""
    notification_canary_outbox_id: str = ""
    # 考核周期调度（P1-2）：自动开启任务 / 截止倒计时 / 逾期告警。
    # 默认关闭 —— 周期调度会改变任务状态（draft → in_progress）并建记录，
    # 属于业务写操作，需显式开启。
    period_scheduler_enabled: bool = False
    period_scheduler_interval_seconds: int = 3600
    # 定时组织架构同步
    feishu_sync_enabled: bool = False
    feishu_sync_interval_seconds: int = 3600
    # ⚠️ **每日固定时刻同步**（2026-11-25）—— 「每天早上 8 点自动同步」。
    #
    # ❌ **为什么不能用「间隔 = 86400 秒」代替**：
    #    间隔模式的触发时刻是**上次跑完的时刻 + 间隔**，会随服务重启漂移 ——
    #    今天 8:00 跑过，明天服务 9 点重启就变成明天 9:00，
    #    之后永远是 9:00，永远等不到「早上 8 点」。
    #    需求是**业务时刻**（「每天早上」），不是「每 24 小时一次」。
    #
    # ✅ **配置格式**：``HH:MM``（24 小时制，本地时间）。
    #    留空 = 退回间隔模式（``feishu_sync_interval_seconds``）。
    feishu_sync_daily_time: str = ""
    # ⚠️ **每日模式的「到点窗口」宽度（分钟）** ——
    #    不是「8:00:00 那一刻」而是「8:00 ~ 8:10」。
    #    ⇒ 服务在 8:00 之后才启动、或调度线程那一刻卡住，都不会漏跑当天同步。
    feishu_sync_daily_window_minutes: int = 10
    # 飞书审批 webhook 的 Verification Token。
    # 未配置时 `/v1/feishu/approval/events` 返回 503（不放行），
    # 避免出现任何人都能改审批状态的未鉴权写入入口。
    #
    # ⚠️ **Verification Token 是「应用级」的，不是「订阅级」的** ——
    #    飞书后台「开发配置 > 事件与回调 > 加密策略」里，**一个应用只有一个**。
    #    官方原文：「Verification Token 是应用的验证标识。开发者后台会为应用
    #    自动生成 Verification Token」。
    #    且「每个应用只能配置一个请求地址，该应用订阅的所有事件都会发送到
    #    该请求地址」—— 所以审批 / 通讯录 / 机器人**共用同一个 Token**。
    #
    #    隔离靠的是**端点路径 + 事件类型白名单**，不是 Token。
    feishu_approval_webhook_token: str = ""
    # 飞书事件的 Encrypt Key（后台「事件与回调 > 加密策略」）。
    # 配置了加密策略时，飞书推送的 body 只有 `{"encrypt": "..."}`，
    # 必须先 AES-256-CBC 解密才能拿到事件内容与 Verification Token；
    # 同时启用 SHA-256 签名校验（X-Lark-Signature）。
    # 留空表示明文模式（仅比对 Verification Token）。
    feishu_encrypt_key: str = ""
    # 飞书通讯录事件的 Verification Token（R18 事件驱动增量）。
    # 未配置时 `/v1/feishu/contact/events` 返回 503（不放行）——
    # 否则任何人都能伪造通讯录变更把员工改成离职。
    #
    # ⚠️ 同上：Token 是应用级的，三条链路共用同一个。
    feishu_contact_event_token: str = ""
    # 事件队列消费间隔（秒）：后台消费者多久扫一次待处理事件。
    feishu_event_consume_interval_seconds: int = 60
    # 是否启用事件驱动增量消费。关闭时事件仍然入队（不丢），
    # 但不自动消费 —— 可作为应急开关。
    feishu_event_consume_enabled: bool = True

    # ─────────────────────────────────────────────────────────────
    # 飞书机器人（FS-3 工作台会话填表）
    #
    # ⚠️ **Verification Token 是应用级的 —— 三条链路只能共用一个**。
    #   官方原文（飞书开放平台「将事件发送至开发者服务器」）：
    #   「Verification Token 是应用的验证标识。开发者后台会为应用自动生成
    #    Verification Token」；且「每个应用只能配置一个请求地址，
    #    该应用订阅的所有事件都会发送到该请求地址」。
    #
    #   ⇒ 同一个飞书应用里，审批 / 通讯录 / 机器人**必然共用**一个 Token。
    #   ⇒ 想给机器人「独立 Token」只有一条路：**新建一个飞书应用**。
    #     本项目当前**只有一个**应用，所以：
    #
    #   **隔离靠端点路径 + 事件类型白名单，不靠 Token。**
    #   - `/v1/feishu/bot/events` 只处理 `im.message.receive_v1` 与
    #     `card.action.trigger`，其余事件类型一律忽略；
    #   - `/v1/feishu/approval/events` 只处理 `approval.approval.updated_v4`；
    #   - `/v1/feishu/contact/events` 只处理 `contact.*`。
    #
    #   ⇒ 排障时靠**日志里的事件类型**分辨是哪条链路，不是靠 Token。
    #
    #   ⚠️ 本配置项**留空时回退到审批 Token**（同一个应用下两者本就相同），
    #   避免运维时「配了一个 Token 却漏配另一个」导致 503。
    #   独立应用部署时，填**该应用自己的** Token 即可。
    # ─────────────────────────────────────────────────────────────
    # 机器人事件的 Verification Token。
    # 留空 → 回退用 `feishu_approval_webhook_token`（同一应用下二者相同）。
    # 两者都为空 → `/v1/feishu/bot/events` 返回 503（不放行）——
    # 否则任何人都能伪造卡片回调写入草稿。
    feishu_bot_event_token: str = ""
    # 机器人会话有效期（分钟）。超过则视为过期，员工重新发起即可。
    feishu_bot_session_ttl_minutes: int = 120
    # 机器人是否启用。关闭时 bot/events 只做握手，不处理业务事件。
    feishu_bot_enabled: bool = True

    # ─────────────────────────────────────────────────────────────
    # 飞书 OAuth（网页免登，FS-1 / FS-2）
    #
    # ⚠️ 留空时 `/v1/feishu/auth/url` 返回 503（不放行）——
    # 与「审批/通讯录 webhook 未配 Token 就 503」保持同一套安全语义：
    # **宁可明确失败，也不要留一个半残的免登入口。**
    # ─────────────────────────────────────────────────────────────
    # 授权成功后的回调地址（逗号分隔多个）。
    # ⚠️ 必须与飞书后台「网页应用 > 授权页面 > 重定向 URL」**完全一致**，
    #    否则飞书会直接拒绝授权（错误在飞书侧，不在本地日志里）。
    feishu_oauth_redirect_uris: str = ""
    # state 签名密钥。留空时回退到 auth_token_secret。
    # ⚠️ 独立配置的意义：state 与登录 token 的密钥**隔离**，
    #    避免一处泄露波及另一处。
    feishu_oauth_state_secret: str = ""
    # state 有效期（秒）。建议 600（10 分钟）——
    # 够用户完成授权，又不会给重放留太久窗口。
    feishu_oauth_state_ttl_seconds: int = 600
    # 申请的 OAuth scope（逗号分隔）。
    # 免登只需要读用户基本信息；多申请会被飞书后台审核卡住。
    feishu_oauth_scopes: str = "contact:user.base:readonly"
    # ⚠️ **不要在这里加「前端免登回调页地址」配置** ——
    #   `/v1/feishu/auth/url` 的 `redirect_uri` 是**请求参数**（走白名单校验），
    #   前端传什么就跳回什么。多一个「服务端配置的默认回调」只会造成
    #   两套来源互相打架 —— 且那个值根本没人读（配置了也不生效，纯误导）。
    #   前端免登入口本身是 FS-2 的任务。

    # ─────────────────────────────────────────────────────────────
    # 同步失败告警
    #
    # 告警本身**总是**入库（可在接口里查询），外部通知（飞书 IM）
    # 是可选的 —— 没配置接收人就不发，不会报错。
    # ─────────────────────────────────────────────────────────────
    # 告警去重抑制窗口（秒）：同一 dedup_key 在此窗口内不重复告警。
    feishu_sync_alert_suppress_seconds: int = 1800
    # 连续多少轮定时同步失败后触发「调度器连续失败」告警。
    feishu_sync_alert_consecutive_failures: int = 3
    # 飞书 IM 告警接收人（open_id 或 chat_id）。留空则不发送外部通知。
    feishu_sync_alert_receiver_id: str = ""
    # 接收人 ID 类型：open_id / user_id / chat_id。
    feishu_sync_alert_receiver_type: str = "chat_id"
    # 单条失败记录的积压告警阈值：待处理失败记录达到该数量时告警。
    feishu_sync_alert_error_backlog_threshold: int = 10

    # ─────────────────────────────────────────────────────────────
    # 离职员工状态判定口径（待业务确认）
    #
    # ⚠️ 以下三项的默认值是**当前代码的实际行为**，
    #    不是业务已确认的结论。业务确认后只需改环境变量。
    # ─────────────────────────────────────────────────────────────
    # is_unjoin（未加入企业）是否按离职处理。
    # False = 按 inactive（未入职，可自动恢复，当前默认）；
    # True  = 按 exited（视为已离开，需人工复职）。
    feishu_sync_unjoin_as_exited: bool = False
    # 离职是否禁用账号（同时 token_version 递增，使已签发 token 失效）。
    feishu_sync_exited_disables_account: bool = True
    # 冻结/未激活是否禁用账号。
    feishu_sync_inactive_disables_account: bool = True

    # ─────────────────────────────────────────────────────────────
    # 飞书绩效方案自动同步（FS-8）
    #
    # 目标：飞书上的部门绩效方案文档 → 全自动进系统
    #     文档变更 → 自动建版本 → 自动发布 → 到周期自动发起考核
    #
    # ⚠️ **默认全部关闭** —— 这条链路会**写业务数据**（建方案版本、
    #    发布方案、发起考核），必须显式开启。
    #
    # ⚠️ **权限现实（2026-11-20 实测，见 scripts/probe_feishu_docs.py）**
    #     ✅ wiki get_node        → 变更检测可用
    #     ❌ wiki 空间/节点列表    → 缺 wiki:wiki:readonly（自动发现不可用）
    #     ❌ docx/drive/sheets    → 缺对应 scope（正文取不到）
    # ⇒ 本应用当前**只能做变更检测**，正文抓取需 IT 补权限。
    # ─────────────────────────────────────────────────────────────
    # 是否启用飞书方案自动同步定时任务。
    feishu_scheme_sync_enabled: bool = False
    # 同步间隔（秒）。不得低于 60 秒，避免打爆飞书接口。
    feishu_scheme_sync_interval_seconds: int = 1800
    # 要扫描的飞书知识空间 ID（用于自动发现部门方案文档）。
    # 留空则不启用自动发现，只同步已人工绑定的来源。
    feishu_scheme_sync_space_id: str = ""
    # 自动发现时使用的周期类型。
    feishu_scheme_sync_period_type: str = "monthly"
    # ⚠️ **是否自动发布**（用户已确认要全自动）。
    #    False = 只建草稿，等人工在网页端确认发布（保守模式）。
    #    True  = 建版本后立即发布生效。
    feishu_scheme_sync_auto_publish: bool = True
    # 自动发起考核：方案发布后是否按周期自动创建考核任务。
    feishu_scheme_sync_auto_start_task: bool = False
    # 自动发起考核时，自评与主管评分的截止天数（相对周期结束日）。
    feishu_scheme_sync_self_review_days: int = 3
    feishu_scheme_sync_manager_review_days: int = 7
    # 同步告警接收人（open_id / chat_id）。留空则只入库不外发。
    feishu_scheme_sync_alert_receiver_id: str = ""
    feishu_scheme_sync_alert_receiver_type: str = "chat_id"

    # ─────────────────────────────────────────────────────────────
    # 飞书绩效方案自动同步（FS-8）
    #
    # 目标：飞书上的部门绩效方案文档 → 全自动进系统
    #     文档变更 → 自动建版本 → 自动发布 → 到周期自动发起考核
    #
    # ⚠️ **默认全部关闭** —— 这条链路会**写业务数据**（建方案版本、
    #    发布方案、发起考核），必须显式开启。
    #
    # ⚠️ **权限现实（2026-11-20 实测，见 scripts/probe_feishu_docs.py）**
    #     ✅ wiki get_node        → 变更检测可用
    #     ❌ wiki 空间/节点列表    → 缺 wiki:wiki:readonly（自动发现不可用）
    #     ❌ docx/drive/sheets    → 缺对应 scope（正文取不到）
    # ⇒ 本应用当前**只能做变更检测**，正文抓取需 IT 补权限。
    # ─────────────────────────────────────────────────────────────
    # 是否启用飞书方案自动同步定时任务。
    feishu_scheme_sync_enabled: bool = False
    # 同步间隔（秒）。不得低于 60 秒，避免打爆飞书接口。
    feishu_scheme_sync_interval_seconds: int = 1800
    # 要扫描的飞书知识空间 ID（用于自动发现部门方案文档）。
    # 留空则不启用自动发现，只同步已人工绑定的来源。
    feishu_scheme_sync_space_id: str = ""
    # 自动发现时使用的周期类型。
    feishu_scheme_sync_period_type: str = "monthly"
    # ⚠️ **是否自动发布**（用户已确认要全自动）。
    #    False = 只建草稿，等人工在网页端确认发布（保守模式）。
    #    True  = 建版本后立即发布生效。
    feishu_scheme_sync_auto_publish: bool = True
    # 自动发起考核：方案发布后是否按周期自动创建考核任务。
    feishu_scheme_sync_auto_start_task: bool = False
    # 自动发起考核时，自评与主管评分的截止天数（相对周期结束日）。
    feishu_scheme_sync_self_review_days: int = 3
    feishu_scheme_sync_manager_review_days: int = 7
    # 同步告警接收人（open_id / chat_id）。留空则只入库不外发。
    feishu_scheme_sync_alert_receiver_id: str = ""
    feishu_scheme_sync_alert_receiver_type: str = "chat_id"

    # ─────────────────────────────────────────────────────────────
    # 百度网盘绩效校验（BP-1）
    #
    # 目标：网盘里的成片文件数 → 校验员工申报的产出数量
    #     网盘文件 → MetricValue（算分集） → 与申报值比对 → 提示主管
    #
    # ⚠️ **默认全部关闭** —— 未配置凭证时整条链路不启动。
    #
    # ⚠️ **目录权限现实（百度官方，2026-11-20 查证）**
    #     - 应用只能访问「权限内目录」，默认是 ``/apps/{应用名}``
    #     - 授权页面里勾选的目录 → 挂到 ``/apps/{应用名}/子目录``
    #     - 递归接口 ``multimedia?method=listall`` **必须**在 ``/apps`` 下
    # ⇒ 想校验网盘里的成片目录，必须在**授权时勾选**它。
    # ─────────────────────────────────────────────────────────────
    # 是否启用百度网盘取数校验。
    baidu_pan_enabled: bool = False
    # 应用凭证（百度网盘开放平台 → 控制台 → 应用详情页 → 基础信息）。
    baidu_pan_app_key: str = ""
    baidu_pan_app_secret: str = ""
    baidu_pan_app_id: str = ""
    # ⚠️ **应用名** —— 平台为应用生成的默认目录是 ``/apps/{应用名}``。
    #    必须与百度开放平台里填的「应用名称」**完全一致**。
    baidu_pan_app_name: str = "绩效校验"
    # OAuth 回调地址。留空则用 ``oob``（授权页直接显示授权码，无需公网域名）。
    baidu_pan_redirect_uri: str = ""
    # ⚠️ **要校验的网盘目录**（绝对路径，如 ``/成品-小瑶``）。
    #
    # ⚠️ **不要加 ``/apps/{应用名}`` 前缀** —— 实测（2026-11-20）：
    #     用户授权后 ``file?method=list`` 能读**任意授权目录**，
    #     而 ``multimedia?method=listall`` 反而读不了业务目录（报 20020）。
    #     ⇒ 递归用自建下钻，路径直接配网盘里的业务目录。
    baidu_pan_watch_dir: str = ""
    # 计数规则：只统计这些扩展名的文件（逗号分隔）。留空则统计全部非目录文件。
    baidu_pan_count_extensions: str = "mp4,mov,m4v,avi,mkv,flv,wmv"
    # 计数规则：文件名包含这些关键词的文件**不计入**（逗号分隔）。
    # 用于排除「返工」「废片」等。
    baidu_pan_exclude_keywords: str = "返工,废片,废弃,作废,rework,_v2,_v3,副本"
    # 取数频率（秒）。百度接口有频控（未审核应用 10 次/小时）。
    baidu_pan_sync_interval_seconds: int = 21600
    # ⚠️ **是否允许自动刷新 token** —— refresh_token 只能用一次，
    #    刷新后必须换成响应里的新值。换错会导致下次刷新失败。
    baidu_pan_auto_refresh_token: bool = True

    # ─────────────────────────────────────────────────────────────
    # 网盘剪辑工作量统计（BP-2）
    # ─────────────────────────────────────────────────────────────
    # ⚠️ **与 BP-1 分开开关** —— BP-1 是「部门总量校验」，
    #    BP-2 是「按人拆的工作量统计」，业务上可以各自独立启停。
    pan_workload_enabled: bool = False
    # 采集频率（秒）。百度接口有频控（未审核应用 10 次/小时）⇒ 下限 1 小时。
    pan_workload_sync_interval_seconds: int = 21600

    # ─────────────────────────────────────────────────────────────
    # ★ 飞书表格填报（2026-11-21）—— 剪辑人员以**表格**形式填报。
    # ⚠️ **与网盘采集独立** —— 一个读飞书表格，一个扫网盘，互不影响。
    # ⚠️ **默认 1 小时** —— 剪辑填完表希望「尽快看到数据进来」，
    #    但飞书开放平台有频控 ⇒ 代码里下限 10 分钟。
    feishu_metric_sync_interval_seconds: int = 3600

    # ─────────────────────────────────────────────────────────────
    # ★ 飞书审批回流定时补偿（2026-11-24，FS-7）—— 主动拉取
    #   「webhook 漏推」的审批实例，把卡在审批中的计划救回来。
    #
    # ⚠️ **为什么需要** —— 审批回流原本**只靠** webhook 事件推送，
    #    而 webhook 不可靠：飞书侧丢事件、投递失败不补发、
    #    服务重启期间的事件永久丢失。
    #    ⇒ 员工在飞书点了「通过」，本系统却一直卡在审批中。
    #
    # ⚠️ **默认关闭** —— 补偿会**主动调用**飞书审批接口
    #    （消耗频控配额）**并写入业务状态** ⇒ 必须业务显式开启。
    feishu_approval_compensation_enabled: bool = False
    # ⚠️ **轮询间隔（秒）** —— 600s（10 分钟）。
    #    ⇒ 最多让一个审批状态延迟 10 分钟才被补上，业务上可接受。
    feishu_approval_compensation_interval_seconds: int = 600
    # ⚠️ **陈旧阈值（秒）** —— 绑定行超过这个时间没更新才去拉。
    #    ⇒ 刚回流过的实例不会被重复拉（省飞书频控配额）。
    feishu_approval_compensation_stale_seconds: int = 300
    # ⚠️ **单轮最多补偿实例数** —— 防止一次积压几百个时
    #    一轮全拉完触发频控，且这一轮跑太久会推迟下一轮。
    feishu_approval_compensation_max_per_run: int = 50

    # ─────────────────────────────────────────────────────────────
    # ★ 绩效评定表附件格式（2026-11-24，FS-6）—— N8「绩效等级已发布」
    #   通知时，除了文字还发一份评定表附件。
    #
    # ⚠️ **默认 xlsx** —— 可二次计算、可筛选、可归档。
    #    ⚠️ PDF 需要额外的 ``reportlab`` 依赖，且中文字体在服务器上
    #    常常缺字（渲染成方块 ⇒ 文档不可读）。
    #    ⇒ 即便配成 pdf，缺依赖/缺字体时也会**自动降级为 xlsx**。
    feishu_rating_attachment_type: str = "xlsx"

    # ─────────────────────────────────────────────────────────────
    # ★ 企业知识库（2026-11-27）
    #
    # ⚠️ **为什么默认关闭**
    #   知识库是**写业务数据**的链路（上传文档 → 切块 → 建索引 →
    #   进 AI 检索），必须由业务显式开启。
    #
    # ⚠️ **向量库**
    #   本系统的检索用**内置 BM25 索引**（零外部依赖），
    #   所以**不需要** Qdrant / Milvus 配置。
    #   ⇒ 部署时只需给一个 Vault 路径即可。
    # ─────────────────────────────────────────────────────────────
    # ⚠️ **默认开启**
    #   企业知识库是**系统自带的核心功能**（三个页面 + 检索验证都在线），
    #   默认关闭会让管理员一进页面就看到「未启用」⇒ 功能等于没有。
    #
    #   ⚠️ **与其它开关的区别**
    #   ``feishu_daily_push_enabled`` 之类是**外部集成**开关
    #   （不开会往飞书发消息 ⇒ 打扰用户 ⇒ 必须默认关）；
    #   知识库是**本地数据**（上传的文件只落自己的盘）⇒ 默认开。
    #
    #   ⇒ 这个开关是**熔断阀**：出问题时把它关掉即可止血。
    knowledge_enabled: bool = True
    # Obsidian Vault 根目录（留空 = 不做 Vault 同步，只支持页面上传）。
    knowledge_vault_path: str = ""
    # 页面上传文件的落盘根目录。
    knowledge_upload_dir: str = str(BACKEND_ROOT / "data" / "knowledge")
    # 归档保留天数（删除的文档源文件在归档里保留多久）。
    knowledge_archive_days: int = 30

    # ─────────────────────────────────────────────────────────────
    # ★ 导出产物落盘目录（2026-11-26）
    #
    # ⚠️ **为什么文件不入库**
    #   导出产物是**文件字节**（PDF / Word / Zip 资料包），
    #   塞进数据库会让库体积失控、备份变慢 ⇒ 只把元信息入库，
    #   文件本体落盘，库里存相对路径 + SHA-256 校验值。
    #
    # ⚠️ **默认落在 backend/data/exports/**
    #   与数据库同目录层级，方便运维一起备份。
    export_output_dir: str = str(BACKEND_ROOT / "data" / "exports")
    # 产物保留天数（到期视为过期，下载会失败）。
    export_retention_days: int = 7

    # ─────────────────────────────────────────────────────────────
    # ★ 每日填报提醒推送（2026-11-23）—— 每天给剪辑推一张
    #   「表格 + 表单」卡，主管收到「谁还没填」汇总。
    #
    # ⚠️ **默认关闭** —— 推送是**主动打扰用户**的操作，
    #    必须由业务显式开启（配置错了会天天刷屏）。
    feishu_daily_push_enabled: bool = False
    # ⚠️ **推送时刻（本地时间 HH:MM）** —— 没定业务时间，先给默认值。
    #    ⚠️ 用的是**服务器本地时间**，部署时注意时区。
    feishu_daily_push_time: str = "17:30"
    # ⚠️⚠️ **「哪些岗位要收填报提醒」已彻底移除**（2026-11-23）
    #
    # ❌ **为什么删掉**：岗位名单写死在配置里 = 岗位一变就得改代码发版。
    #    用户原话：「哪个部门都有不一样的考核方式，肯定是不一样的功能呀」
    #
    # ✅ **现在口径存在 ``pan_workload_policy`` 表里**（部门 × 岗位）：
    #       reporter（要填）/ reviewer（只审）/ none（不参与）
    #    ⇒ 改完立即生效，不发版。
    #
    # ⚠️ **不要把它加回来** —— 仓库有「死配置项检测」测试
    #    （``test_feishu_oauth.py::test_oauth_config_surface_has_no_dead_settings``），
    #    加一个没人读的配置项会直接让 CI 挂掉。

    @model_validator(mode="after")
    def _validate_auth_secret(self):
        """启动即校验认证密钥，避免弱密钥静默上线。"""
        if not self.secure_auth:
            return self
        secret = self.auth_token_secret.strip()
        if secret.lower() in _WEAK_AUTH_SECRETS or len(secret) < 32:
            raise ValueError(
                "AUTH_TOKEN_SECRET 配置不安全：必须为至少 32 位随机字符串，"
                "且不得使用默认值。生产环境请通过环境变量或 Secret 管理系统注入。"
            )
        return self

    @model_validator(mode="after")
    def _validate_agent_tool_secret(self):
        secret = self.agent_tool_secret
        if secret and (
            secret != secret.strip()
            or secret.lower() in _WEAK_AUTH_SECRETS
            or len(secret) < 32
            or secret in (self.auth_token_secret, self.auth_token_secret_prev, self.agent_shared_secret)
        ):
            raise ValueError("AGENT_TOOL_SECRET 必须为至少 32 位独立随机字符串")
        if not 30 <= self.agent_tool_token_ttl_seconds <= 300:
            raise ValueError("AGENT_TOOL_TOKEN_TTL_SECONDS 必须在 30 到 300 秒之间")
        return self

    @model_validator(mode="after")
    def _validate_sync_interval(self):
        """定时同步间隔不得低于 60 秒，避免打爆飞书接口。"""
        if self.feishu_sync_interval_seconds < 60:
            raise ValueError("FEISHU_SYNC_INTERVAL_SECONDS 不得小于 60 秒")
        return self

    @model_validator(mode="after")
    def _validate_sync_daily_time(self):
        """每日固定时刻同步的配置校验。

        ⚠️ **配错了不能让服务起不来** —— 这里只做**格式**校验，
        语义校验（小时/分钟范围）在调度器里做，并退回间隔模式兜底。

        为什么格式错误要直接报错：格式错误几乎一定是**配置笔误**
        （写成「8点」「08:00:00」），静默忽略会让用户以为配好了、
        实际却在跑另一个模式 —— 这种"看起来生效其实没生效"的坑
        比启动失败难查得多。
        """
        raw = (self.feishu_sync_daily_time or "").strip()
        if raw:
            parts = raw.split(":")
            if len(parts) != 2 or not all(p.strip().isdigit() for p in parts):
                raise ValueError(
                    "FEISHU_SYNC_DAILY_TIME 应为 HH:MM 格式（如 08:00），留空则用间隔模式"
                )
        if self.feishu_sync_daily_window_minutes < 1:
            raise ValueError("FEISHU_SYNC_DAILY_WINDOW_MINUTES 不得小于 1 分钟")
        return self

    @model_validator(mode="after")
    def _validate_scheme_sync(self):
        """飞书方案自动同步的配置校验。"""
        if self.feishu_scheme_sync_interval_seconds < 60:
            raise ValueError("FEISHU_SCHEME_SYNC_INTERVAL_SECONDS 不得小于 60 秒")
        if self.feishu_scheme_sync_period_type not in ("monthly", "quarterly", "annual"):
            raise ValueError(
                "FEISHU_SCHEME_SYNC_PERIOD_TYPE 应为 monthly/quarterly/annual"
            )
        return self

    @property
    def resolved_database_target(self) -> str:
        """启动日志使用的数据库定位信息（不包含密码）。"""
        if self.database_url.startswith("sqlite"):
            raw = self.database_url.split("///", 1)[-1]
            if raw in ("", ":memory:"):
                return ":memory:"
            return str(Path(raw).resolve())
        # 非 SQLite：只显示主机/库名，不含用户名与密码。
        tail = self.database_url.rsplit("@", 1)[-1]
        return tail.split("?", 1)[0]


settings = Settings()
