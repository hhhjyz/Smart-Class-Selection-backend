"""外部依赖适配层：A/B 服务与 LLM 的 HTTP 客户端。

实现 domain.ports 的 client 接口，含超时、重试、熔断；adapter 把上游
响应转成 domain 实体，向上层屏蔽外部 JSON 结构。
"""
