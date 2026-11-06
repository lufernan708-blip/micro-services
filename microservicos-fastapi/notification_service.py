import asyncio
import json
import logging
import os
from typing import Any

import aio_pika

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger("notification-service")

RABBITMQ_URL = os.getenv("RABBITMQ_URL", "amqp://guest:guest@127.0.0.1:5672/")
ORDERS_EXCHANGE = "orders"
NOTIFICATIONS_QUEUE = "order-notifications"
RECONNECT_DELAY_SECONDS = 5


async def process_order_created(message: aio_pika.abc.AbstractIncomingMessage) -> None:
    async with message.process(requeue=True):
        event: dict[str, Any] = json.loads(message.body)
        if event.get("event_type") != "order.created":
            raise ValueError(f"Unsupported event type: {event.get('event_type')}")

        order = event["order"]
        logger.info(
            "Enviando confirmação: pedido #%s, item '%s', usuário #%s, valor %.2f (criado por %s, evento %s)",
            order["id"],
            order["item_name"],
            order["user_id"],
            order["amount"],
            event["created_by"],
            event["event_id"],
        )


async def consume_notifications() -> None:
    while True:
        connection: aio_pika.abc.AbstractRobustConnection | None = None
        try:
            connection = await aio_pika.connect_robust(RABBITMQ_URL)
            channel = await connection.channel()
            await channel.set_qos(prefetch_count=10)

            exchange = await channel.declare_exchange(
                ORDERS_EXCHANGE,
                aio_pika.ExchangeType.TOPIC,
                durable=True,
            )
            queue = await channel.declare_queue(NOTIFICATIONS_QUEUE, durable=True)
            await queue.bind(exchange, routing_key="order.created")

            logger.info("Consuming order events from queue '%s'", NOTIFICATIONS_QUEUE)
            async with queue.iterator() as queue_iterator:
                async for message in queue_iterator:
                    await process_order_created(message)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Notification consumer disconnected; retrying")
            await asyncio.sleep(RECONNECT_DELAY_SECONDS)
        finally:
            if connection is not None and not connection.is_closed:
                await connection.close()


if __name__ == "__main__":
    asyncio.run(consume_notifications())
