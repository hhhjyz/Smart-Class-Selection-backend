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

    # 上游 A（基础信息 info_service）：前缀 /api/v1/info、响应壳 {code,message,data}、
    # 服务间走 Service Token（Authorization: Bearer）直连（data-provision 用 service token）。
    # 已核对其仓库 group1-base/info_service。
    info_service_base_url: str = "http://info-service:8000"
    info_user_path: str = "/api/v1/info/users/{id}"  # → UserResponse(user_no, profile.full_name)
    info_course_path: str = "/api/v1/info/courses/{id}"  # → CourseResponse(course_code, course_name, credit)
    info_offerings_path: str = "/api/v1/info/offerings"  # → OfferingResponse(course_id, course_code/name, capacity)
    info_training_programs_path: str = "/api/v1/info/data-provision/training-programs"  # 培养方案（供 C 组）
    info_service_timeout_ms: int = 2000
    # 服务间令牌：A 组 POST /api/v1/auth/sys/login（client_id/secret）签发 service token。
    # 这里直接配置已签发的 token（运行期可换成登录换取+缓存刷新）。空=不带 Authorization。
    info_service_token: str = ""

    # 上游 B（排课 zjuse-schedule）：开课时段/教室的权威来源。真实契约（已核对其仓库）——
    # 前缀 /api/v1、响应壳 {code,msg,data}（code=0 成功）、鉴权走网关头 X-User-Id/X-User-Role。
    # B 组注释明确「下游(智能选课组)可通过 /schedule/entries 拉取课表数据」。
    schedule_service_base_url: str = "http://schedule-service:8000"
    schedule_entries_path: str = "/api/v1/schedule/entries"
    schedule_classrooms_path: str = "/api/v1/classrooms"
    schedule_service_timeout_ms: int = 2000
    # 服务身份：B 组 get_current_user 要求网关头存在，服务间调用以管理员身份透传。
    schedule_service_user_id: str = "course-selection-svc"
    schedule_service_role: str = "ADMIN"

    upstream_max_retries: int = 2
    # 连续多少次 5xx/网络错触发熔断（保留给 LLM 等已有外部依赖）
    circuit_break_threshold: int = 5
    circuit_break_cooldown_s: int = 30

    # 鉴权（网关透传的 header 名）
    jwt_user_header: str = "X-User-ID"
    jwt_role_header: str = "X-User-Role"
    request_id_header: str = "X-Request-ID"

    # 仅开发：无网关时，从 Bearer token 推断身份（生产由网关注入 X-User-ID，保持 False）
    dev_auth_from_token: bool = False
    # 仅开发：CORS 允许的来源正则（前端直连后端时需要）；空=不启用 CORS（生产由网关处理）
    dev_cors_origin_regex: str = ""

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
