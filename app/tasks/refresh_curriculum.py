"""培养方案规则缓存刷新占位。

curriculum_rules TTL 24h，由本任务定期从 A 组拉取刷新。具体 upsert SQL
随 A 组接口字段最终敲定后补全（见风险登记）。
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


async def refresh_curriculum_rules() -> int:
    """刷新培养方案规则缓存。返回刷新条数。"""
    # 实现依赖 A 组 curriculum 接口字段最终确定；当前为调度骨架。
    logger.info("培养方案规则刷新触发", extra={"event": "curriculum.refresh"})
    return 0
