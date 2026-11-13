"""应用配置。"""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # 数据库
    database_url: str = "sqlite:///./data/perf.db"

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

    # 飞书集成
    feishu_app_id: str = ""
    feishu_app_secret: str = ""
    feishu_tenant_id: str = ""


settings = Settings()
