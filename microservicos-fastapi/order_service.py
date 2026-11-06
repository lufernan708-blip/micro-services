import asyncio
import json
import logging
import os
import sqlite3
from contextlib import asynccontextmanager, closing
from datetime import datetime, timezone
from typing import Annotated, Any
from uuid import uuid4

import aio_pika
import httpx
from fastapi import FastAPI, Header, HTTPException, status
from pydantic import BaseModel, Field

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger("order-service")

USER_SERVICE_URL = os.getenv("USER_SERVICE_URL", "http://127.0.0.1:8001").rstrip("/")
DATABASE_PATH = os.getenv("ORDERS_DATABASE_PATH", "orders.db")
RABBITMQ_URL = os.getenv("RABBITMQ_URL", "amqp://guest:guest@127.0.0.1:5672/")
ORDERS_EXCHANGE = "orders"
PUBLISH_RETRY_SECONDS = 3


def get_connection() -> sqlite3.Connection:
    connection = sqlite3.connect(DATABASE_PATH, timeout=10)
    connection.row_factory = sqlite3.Row
    return connection


def create_http_client(**kwargs) -> httpx.AsyncClient:
    return httpx.AsyncClient(**kwargs)


def init_db() -> None:
    with closing(get_connection()) as connection, connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                item_name TEXT NOT NULL,
                amount REAL NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS outbox_events (
                id TEXT PRIMARY KEY,
                event_type TEXT NOT NULL,
                payload TEXT NOT NULL,
                created_at TEXT NOT NULL,
                published_at TEXT
            )
            """
        )


init_db()


class OrderCreate(BaseModel):
    user_id: int = Field(gt=0)
    item_name: str = Field(min_length=1, max_length=200)
    amount: float = Field(gt=0)


def get_pending_events(limit: int = 100) -> list[sqlite3.Row]:
    with closing(get_connection()) as connection:
        return connection.execute(
            """
            SELECT id, event_type, payload
            FROM outbox_events
            WHERE published_at IS NULL
            ORDER BY created_at, id
            LIMIT ?
            """,
            (limit,),
        ).fetchall()


def mark_event_published(event_id: str) -> None:
    with closing(get_connection()) as connection, connection:
        connection.execute(
            "UPDATE outbox_events SET published_at = ? WHERE id = ?",
            (datetime.now(timezone.utc).isoformat(), event_id),
        )


async def publish_outbox_events() -> None:
    while True:
        connection: aio_pika.abc.AbstractRobustConnection | None = None
        try:
            connection = await aio_pika.connect_robust(RABBITMQ_URL)
            channel = await connection.channel()
            exchange = await channel.declare_exchange(
                ORDERS_EXCHANGE,
                aio_pika.ExchangeType.TOPIC,
                durable=True,
            )

            while True:
                pending_events = get_pending_events()
                if not pending_events:
                    await asyncio.sleep(PUBLISH_RETRY_SECONDS)
                    continue

                for event in pending_events:
                    await exchange.publish(
                        aio_pika.Message(
                            body=event["payload"].encode("utf-8"),
                            content_type="application/json",
                            delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
                            message_id=event["id"],
                            type=event["event_type"],
                        ),
                        routing_key=event["event_type"],
                        mandatory=True,
                    )
                    mark_event_published(event["id"])
                    logger.info(
                        "Published event %s (message_id=%s)",
                        event["event_type"],
                        event["id"],
                    )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Could not publish order events; retrying shortly")
            await asyncio.sleep(PUBLISH_RETRY_SECONDS)
        finally:
            if connection is not None and not connection.is_closed:
                await connection.close()


@asynccontextmanager
async def lifespan(_: FastAPI):
    publisher_task = asyncio.create_task(publish_outbox_events())
    try:
        yield
    finally:
        publisher_task.cancel()
        try:
            await publisher_task
        except asyncio.CancelledError:
            pass


app = FastAPI(title="Order Service", version="1.0.0", lifespan=lifespan)


@app.post("/orders", status_code=status.HTTP_201_CREATED)
async def create_order(
    order: OrderCreate,
    authenticated_user: Annotated[str, Header(alias="X-Authenticated-User")],
) -> dict[str, Any]:
    try:
        async with create_http_client(timeout=5.0) as client:
            user_response = await client.get(
                f"{USER_SERVICE_URL}/users/{order.user_id}"
            )
    except httpx.RequestError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Serviço de Usuários temporariamente indisponível.",
        ) from exc

    if user_response.status_code == status.HTTP_404_NOT_FOUND:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Usuário não existe. Não é possível criar o pedido.",
        )
    if user_response.status_code != status.HTTP_200_OK:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Erro ao comunicar com User Service.",
        )

    event_id = str(uuid4())
    response_body: dict[str, Any]
    with closing(get_connection()) as connection, connection:
        cursor = connection.execute(
            "INSERT INTO orders (user_id, item_name, amount) VALUES (?, ?, ?)",
            (order.user_id, order.item_name, order.amount),
        )
        order_id = cursor.lastrowid
        response_body = {
            "id": order_id,
            "user_id": order.user_id,
            "item_name": order.item_name,
            "amount": order.amount,
        }
        event_payload = {
            "event_id": event_id,
            "event_type": "order.created",
            "occurred_at": datetime.now(timezone.utc).isoformat(),
            "order": response_body,
            "created_by": authenticated_user,
        }
        connection.execute(
            """
            INSERT INTO outbox_events (id, event_type, payload, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (
                event_id,
                "order.created",
                json.dumps(event_payload),
                event_payload["occurred_at"],
            ),
        )

    return response_body


@app.get("/orders/user/{user_id}")
def get_orders_by_user(user_id: int) -> dict[str, Any]:
    with closing(get_connection()) as connection:
        rows = connection.execute(
            """
            SELECT id, user_id, item_name, amount
            FROM orders
            WHERE user_id = ?
            ORDER BY id
            """,
            (user_id,),
        ).fetchall()
    return {"user_id": user_id, "orders": [dict(row) for row in rows]}
