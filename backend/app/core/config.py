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
