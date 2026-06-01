"""RabbitMQ 连接与 Outbox 投递。

C → D/F 的事件投递走 Outbox 模式：业务事务只写 outbox_events 表，
独立投递器周期扫描 pending 行推送至此处声明的 exchange。
对应《08 构件》Outbox Publisher 与《10 跨组接口契约》。
"""

from __future__ import annotations

import aio_pika

from app.core.config import get_settings

_connection: aio_pika.abc.AbstractRobustConnection | None = None
_channel: aio_pika.abc.AbstractChannel | None = None
_exchange: aio_pika.abc.AbstractExchange | None = None


async def open_mq() -> None:
    """建立 robust 连接并声明 topic exchange（durable）。幂等。"""
    global _connection, _channel, _exchange
    if _connection is not None:
        return
    settings = get_settings()
    _connection = await aio_pika.connect_robust(settings.rmq_url)
    _channel = await _connection.channel(publisher_confirms=True)
    _exchange = await _channel.declare_exchange(
        settings.rmq_exchange_enrollment,
        aio_pika.ExchangeType.TOPIC,
        durable=True,
    )


async def close_mq() -> None:
    global _connection, _channel, _exchange
    if _connection is not None:
        await _connection.close()
        _connection = _channel = _exchange = None


async def publish(routing_key: str, body: bytes) -> None:
    """发布一条持久化消息到选课事件 exchange。

    投递确认开启（publisher confirms），失败抛异常由投递器重试。
    """
    if _exchange is None:
        raise RuntimeError("MQ 未初始化，请先 await open_mq()")
    await _exchange.publish(
        aio_pika.Message(
            body=body,
            delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
            content_type="application/json",
        ),
        routing_key=routing_key,
    )
