"""高并发原语：Waiting Room、库存原子扣减、抽签批处理。

实现 domain.ports 的 StockStore / WaitingRoom。全部基于 redis.asyncio
单命令原语，不写 Lua。对应《04 高并发引擎设计》。
"""
