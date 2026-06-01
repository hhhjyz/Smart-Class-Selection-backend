"""应用配置：全部从环境变量 / .env 读取，由 pydantic-settings 校验。

任何端点地址、密钥、限流参数都不得硬编码，统一收口到此处。
对应《07 部署与运维》「.env 必备项」。
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """运行期配置。字段名与 .env 键一一对应（大小写不敏感）。"""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # 服务自身
    service_name: str = "course-selection"
    service_port: int = 8003
    log_level: str = "INFO"
    # api | worker：决定 main.py 的启动入口
    mode: str = "api"

    # 数据库
    pg_dsn: str = "postgres://cs_user:cs_pwd@course-selection-pg:5432/course_selection"
    pg_pool_min: int = 4
    pg_pool_max: int = 32

    # Redis
    redis_url: str = "redis://course-selection-redis:6379/0"

    # RabbitMQ
    rmq_url: str = "amqp://stss:stss@rabbitmq:5672/"
    rmq_exchange_enrollment: str = "enrollment.events"

    # 上游服务（A / B 组）
    info_service_base_url: str = "http://info-service:8001"
    schedule_service_base_url: str = "http://schedule-service:8002"
    info_service_timeout_ms: int = 2000
    schedule_service_timeout_ms: int = 2000
    upstream_max_retries: int = 2
    # 连续多少次 5xx/网络错触发熔断
    circuit_break_threshold: int = 5
    circuit_break_cooldown_s: int = 30

    # 鉴权（网关透传的 header 名）
    jwt_user_header: str = "X-User-ID"
    jwt_role_header: str = "X-User-Role"
    request_id_header: str = "X-Request-ID"

    # LLM
    llm_base_url: str = ""
    llm_api_key: str = ""
    llm_chat_model: str = "gpt-4o-mini"
    llm_embedding_model: str = "text-embedding-3-small"
    llm_timeout_ms: int = 60000
    llm_max_concurrency: int = 32

    # 限流（运行期可被 /admin/throttle 热更新）
    waitroom_tick_ms: int = 200
    waitroom_cap_per_tick: int = 50
    per_user_rps: int = 5

    # AI 配额
    ai_daily_conversation_quota: int = 20
    ai_daily_recommendation_quota: int = 50

    upstream_timeout_default_s: float = Field(default=2.0, exclude=True)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """进程级单例配置。lru_cache 保证只解析一次 .env。"""
    return Settings()
