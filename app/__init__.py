"""智能选课子系统（Smart Course Selection / C 组）。

FastAPI 服务，承担 STSS 中的高并发选课业务。分层遵循依赖倒置：
``api → services/engine → domain.ports ← repositories/integrations``。
"""
