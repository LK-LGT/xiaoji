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

    # 智能体服务
    agent_service_url: str = "http://127.0.0.1:8800"
    agent_shared_secret: str = ""

    # 模型
    model_name: str = "echo-deterministic"
    model_mode: str = "echo"  # echo | null | openai

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
    # 定时组织架构同步
    feishu_sync_enabled: bool = False
    feishu_sync_interval_seconds: int = 3600
    # 飞书审批 webhook 的 Verification Token。
    # 未配置时 `/v1/feishu/approval/events` 返回 503（不放行），
    # 避免出现任何人都能改审批状态的未鉴权写入入口。
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
    feishu_contact_event_token: str = ""
    # 事件队列消费间隔（秒）：后台消费者多久扫一次待处理事件。
    feishu_event_consume_interval_seconds: int = 60
    # 是否启用事件驱动增量消费。关闭时事件仍然入队（不丢），
    # 但不自动消费 —— 可作为应急开关。
    feishu_event_consume_enabled: bool = True

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
    def _validate_sync_interval(self):
        """定时同步间隔不得低于 60 秒，避免打爆飞书接口。"""
        if self.feishu_sync_interval_seconds < 60:
            raise ValueError("FEISHU_SYNC_INTERVAL_SECONDS 不得小于 60 秒")
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
