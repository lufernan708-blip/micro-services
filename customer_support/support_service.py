from __future__ import annotations

import asyncio
from collections import defaultdict
from datetime import datetime, timezone
from enum import StrEnum
from typing import Annotated, Any, Protocol
from uuid import uuid4

import aio_pika
from fastapi import FastAPI, Header, HTTPException, WebSocket, WebSocketDisconnect, status
from pydantic import BaseModel, Field


class TicketPriority(StrEnum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    URGENT = "urgent"


class TicketStatus(StrEnum):
    QUEUED = "queued"
    ASSIGNED = "assigned"
    IN_PROGRESS = "in_progress"
    RESOLVED = "resolved"
    CLOSED = "closed"


class TicketCreate(BaseModel):
    subject: str = Field(min_length=3, max_length=160)
    description: str = Field(min_length=1, max_length=10_000)
    category: str = Field(min_length=1, max_length=80)
    priority: TicketPriority = TicketPriority.NORMAL
    required_skills: set[str] = Field(default_factory=set, max_length=20)
    attachment_ids: list[str] = Field(default_factory=list, max_length=10)


class Ticket(BaseModel):
    id: str
    client_id: str
    subject: str
    description: str
    category: str
    priority: TicketPriority
    required_skills: set[str]
    attachment_ids: list[str]
    status: TicketStatus = TicketStatus.QUEUED
    assigned_agent_id: str | None = None
    created_at: datetime


class MessageCreate(BaseModel):
    body: str = Field(min_length=1, max_length=8_000)


class TicketMessage(BaseModel):
    id: str
    ticket_id: str
    author_id: str
    body: str
    created_at: datetime


class TicketEvent(BaseModel):
    event_id: str
    event_type: str
    occurred_at: datetime
    ticket_id: str
    payload: dict[str, Any]


class Principal(BaseModel):
    id: str


class TicketNotFound(Exception):
    pass


class TicketForbidden(Exception):
    pass


class SupportUnavailable(Exception):
    pass


class AuthenticationError(Exception):
    pass


class SupportRepository(Protocol):
    """Production adapters persist aggregate changes and outbox events atomically."""

    async def open_ticket(self, ticket: Ticket) -> Ticket: ...

    async def append_message(self, message: TicketMessage) -> TicketMessage: ...

    async def get_ticket(self, ticket_id: str) -> Ticket | None: ...

    async def get_messages(self, ticket_id: str) -> list[TicketMessage]: ...

    async def change_status(self, ticket_id: str, new_status: TicketStatus) -> Ticket: ...

    async def mark_waiting(self, ticket_id: str) -> bool: ...

    async def assign_if_queued(self, ticket_id: str, agent_id: str) -> bool: ...

    async def pending_events(self, limit: int = 100) -> list[TicketEvent]: ...

    async def mark_event_published(self, event_id: str) -> None: ...


class EventPublisher(Protocol):
    """Broker adapter publishes a durable event and waits for broker confirmation."""

    async def publish(self, event: TicketEvent) -> None: ...

    async def consume(
        self,
        queue_name: str,
        routing_key: str,
        handler: Any,
    ) -> None: ...



class AgentRegistry(Protocol):
    """Redis adapter must atomically claim the least-active skill-matched agent."""

    async def claim_least_active(self, required_skills: set[str]) -> str | None: ...

    async def release_claim(self, agent_id: str) -> None: ...


class WebSocketAuthenticator(Protocol):
    async def authenticate(self, websocket: WebSocket) -> Principal: ...


class RealtimeHub:
    """Single-process demo hub; production deployments need a cross-process pub/sub adapter."""

    def __init__(self) -> None:
        self._connections: dict[str, set[WebSocket]] = defaultdict(set)
        self._lock = asyncio.Lock()

    async def connect(self, room_id: str, websocket: WebSocket) -> None:
        await websocket.accept()
        async with self._lock:
            self._connections[room_id].add(websocket)

    async def disconnect(self, room_id: str, websocket: WebSocket) -> None:
        async with self._lock:
            connections = self._connections.get(room_id)
            if connections is None:
                return
            connections.discard(websocket)
            if not connections:
                self._connections.pop(room_id, None)

    async def broadcast(self, room_id: str, event: dict[str, Any]) -> None:
        async with self._lock:
            connections = tuple(self._connections.get(room_id, ()))

        results = await asyncio.gather(
            *(connection.send_json(event) for connection in connections),
            return_exceptions=True,
        )
        failed = [
            connection
            for connection, result in zip(connections, results)
            if isinstance(result, BaseException)
        ]
        if failed:
            async with self._lock:
                current = self._connections.get(room_id)
                if current is not None:
                    current.difference_update(failed)
                    if not current:
                        self._connections.pop(room_id, None)


class InMemorySupportRepository:
    """Runnable demo adapter only; replace it with a durable repository/outbox in production."""

    def __init__(self) -> None:
        self.tickets: dict[str, Ticket] = {}
        self.messages: dict[str, list[TicketMessage]] = defaultdict(list)
        self.events: list[TicketEvent] = []
        self.published_event_ids: set[str] = set()
        self._lock = asyncio.Lock()

    def _enqueue(self, event_type: str, ticket_id: str, payload: dict[str, Any]) -> None:
        self.events.append(
            TicketEvent(
                event_id=str(uuid4()),
                event_type=event_type,
                occurred_at=datetime.now(timezone.utc),
                ticket_id=ticket_id,
                payload=payload,
            )
        )

    async def open_ticket(self, ticket: Ticket) -> Ticket:
        async with self._lock:
            if ticket.id in self.tickets:
                raise SupportUnavailable("Ticket identifier collision.")
            self.tickets[ticket.id] = ticket.model_copy(deep=True)
            self._enqueue("ticket.opened", ticket.id, {"ticket": ticket.model_dump(mode="json")})
            return ticket

    async def append_message(self, message: TicketMessage) -> TicketMessage:
        async with self._lock:
            if message.ticket_id not in self.tickets:
                raise TicketNotFound(message.ticket_id)
            self.messages[message.ticket_id].append(message)
            self._enqueue("message.created", message.ticket_id, {"message": message.model_dump(mode="json")})
            return message

    async def get_ticket(self, ticket_id: str) -> Ticket | None:
        async with self._lock:
            ticket = self.tickets.get(ticket_id)
            return ticket.model_copy(deep=True) if ticket else None

    async def get_messages(self, ticket_id: str) -> list[TicketMessage]:
        async with self._lock:
            if ticket_id not in self.tickets:
                raise TicketNotFound(ticket_id)
            return [message.model_copy(deep=True) for message in self.messages[ticket_id]]

    async def change_status(self, ticket_id: str, new_status: TicketStatus) -> Ticket:
        async with self._lock:
            ticket = self.tickets.get(ticket_id)
            if ticket is None:
                raise TicketNotFound(ticket_id)
            ticket.status = new_status
            self._enqueue(
                "ticket.status_changed",
                ticket_id,
                {"status": new_status.value},
            )
            return ticket.model_copy(deep=True)

    async def mark_waiting(self, ticket_id: str) -> bool:
        async with self._lock:
            ticket = self.tickets.get(ticket_id)
            if ticket is None:
                raise TicketNotFound(ticket_id)
            if ticket.status != TicketStatus.QUEUED:
                return False
            self._enqueue("ticket.waiting", ticket_id, {"status": TicketStatus.QUEUED.value})
            return True

    async def assign_if_queued(self, ticket_id: str, agent_id: str) -> bool:
        async with self._lock:
            ticket = self.tickets.get(ticket_id)
            if ticket is None:
                raise TicketNotFound(ticket_id)
            if ticket.status != TicketStatus.QUEUED:
                return False
            ticket.assigned_agent_id = agent_id
            ticket.status = TicketStatus.ASSIGNED
            self._enqueue(
                "ticket.assigned",
                ticket_id,
                {"agent_id": agent_id, "status": TicketStatus.ASSIGNED.value},
            )
            return True

    async def pending_events(self, limit: int = 100) -> list[TicketEvent]:
        async with self._lock:
            pending = [
                event
                for event in self.events
                if event.event_id not in self.published_event_ids
            ]
            return [event.model_copy(deep=True) for event in pending[:limit]]

    async def mark_event_published(self, event_id: str) -> None:
        async with self._lock:
            if not any(event.event_id == event_id for event in self.events):
                raise SupportUnavailable("Outbox event not found.")
            self.published_event_ids.add(event_id)


class TicketRoutingWorker:
    def __init__(
        self,
        repository: SupportRepository,
        agents: AgentRegistry,
    ) -> None:
        self.repository = repository
        self.agents = agents

    async def route(self, ticket_id: str) -> str | None:
        ticket = await self.repository.get_ticket(ticket_id)
        if ticket is None:
            raise TicketNotFound(ticket_id)
        if ticket.status != TicketStatus.QUEUED:
            return ticket.assigned_agent_id

        agent_id = await self.agents.claim_least_active(ticket.required_skills)
        if agent_id is None:
            await self.repository.mark_waiting(ticket_id)
            return None

        try:
            assigned = await self.repository.assign_if_queued(ticket_id, agent_id)
        except Exception:
            await self.agents.release_claim(agent_id)
            raise

        if not assigned:
            await self.agents.release_claim(agent_id)
            latest = await self.repository.get_ticket(ticket_id)
            return latest.assigned_agent_id if latest else None
        return agent_id


class OutboxDispatcher:
    """Worker retries pending events until the broker confirms delivery."""

    def __init__(
        self,
        repository: SupportRepository,
        publisher: EventPublisher,
    ) -> None:
        self.repository = repository
        self.publisher = publisher

    async def dispatch_once(self, limit: int = 100) -> int:
        published = 0
        for event in await self.repository.pending_events(limit):
            await self.publisher.publish(event)
            await self.repository.mark_event_published(event.event_id)
            published += 1
        return published


class RabbitMQEventBus:
    """RabbitMQ adapter; receive the broker URL from deployment secret configuration."""

    def __init__(self, url: str, exchange_name: str = "support.events") -> None:
        self._url = url
        self._exchange_name = exchange_name
        self._connection: aio_pika.abc.AbstractRobustConnection | None = None
        self._channel: aio_pika.abc.AbstractRobustChannel | None = None
        self._exchange: aio_pika.abc.AbstractExchange | None = None
        self._lock = asyncio.Lock()

    async def _get_exchange(self):
        async with self._lock:
            if self._exchange is None or self._connection is None or self._connection.is_closed:
                self._connection = await aio_pika.connect_robust(self._url)
                self._channel = await self._connection.channel(
                    publisher_confirms=True,
                    on_return_raises=True,
                )
                self._exchange = await self._channel.declare_exchange(
                    self._exchange_name,
                    aio_pika.ExchangeType.TOPIC,
                    durable=True,
                )
            return self._exchange

    async def publish(self, event: TicketEvent) -> None:
        exchange = await self._get_exchange()
        await exchange.publish(
            aio_pika.Message(
                body=event.model_dump_json().encode("utf-8"),
                content_type="application/json",
                delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
                message_id=event.event_id,
                type=event.event_type,
            ),
            routing_key=event.event_type,
            mandatory=True,
        )

    async def consume(
        self,
        queue_name: str,
        routing_key: str,
        handler: Any,
    ) -> None:
        connection = await aio_pika.connect_robust(self._url)
        try:
            channel = await connection.channel()
            await channel.set_qos(prefetch_count=20)
            exchange = await channel.declare_exchange(
                self._exchange_name,
                aio_pika.ExchangeType.TOPIC,
                durable=True,
            )
            queue = await channel.declare_queue(queue_name, durable=True)
            await queue.bind(exchange, routing_key=routing_key)
            async with queue.iterator() as iterator:
                async for message in iterator:
                    async with message.process(requeue=True):
                        event = TicketEvent.model_validate_json(message.body)
                        await handler(event)
        finally:
            await connection.close()

    async def close(self) -> None:
        if self._connection is not None and not self._connection.is_closed:
            await self._connection.close()
        self._connection = None
        self._channel = None
        self._exchange = None


class RoutingEventWorker:
    def __init__(self, router: TicketRoutingWorker) -> None:
        self.router = router

    async def handle(self, event: TicketEvent) -> None:
        if event.event_type != "ticket.opened":
            return
        await self.router.route(event.ticket_id)


class RealtimeEventConsumer:
    def __init__(self, hub: RealtimeHub) -> None:
        self.hub = hub

    async def handle(self, event: TicketEvent) -> None:
        await self.hub.broadcast(
            event.ticket_id,
            {
                "event_id": event.event_id,
                "type": event.event_type,
                "occurred_at": event.occurred_at.isoformat(),
                **event.payload,
            },
        )


def create_app(
    repository: SupportRepository,
    hub: RealtimeHub,
    websocket_authenticator: WebSocketAuthenticator,
) -> FastAPI:
    app = FastAPI(title="Customer Support API", version="1.0.0")

    async def get_participant_ticket(ticket_id: str, principal_id: str) -> Ticket:
        ticket = await repository.get_ticket(ticket_id)
        if ticket is None:
            raise TicketNotFound(ticket_id)
        if principal_id not in {ticket.client_id, ticket.assigned_agent_id}:
            raise TicketForbidden(ticket_id)
        return ticket

    @app.post("/tickets", status_code=status.HTTP_202_ACCEPTED, response_model=Ticket)
    async def open_ticket(
        payload: TicketCreate,
        client_id: Annotated[str, Header(alias="X-Authenticated-User")],
    ) -> Ticket:
        ticket = Ticket(
            id=str(uuid4()),
            client_id=client_id,
            **payload.model_dump(),
            created_at=datetime.now(timezone.utc),
        )
        try:
            return await repository.open_ticket(ticket)
        except SupportUnavailable as exc:
            raise HTTPException(status_code=503, detail="Support service unavailable.") from exc

    @app.get("/tickets/{ticket_id}/messages", response_model=list[TicketMessage])
    async def get_ticket_history(
        ticket_id: str,
        principal_id: Annotated[str, Header(alias="X-Authenticated-User")],
    ) -> list[TicketMessage]:
        try:
            await get_participant_ticket(ticket_id, principal_id)
            return await repository.get_messages(ticket_id)
        except TicketNotFound as exc:
            raise HTTPException(status_code=404, detail="Ticket not found.") from exc
        except TicketForbidden as exc:
            raise HTTPException(status_code=403, detail="Not authorized for this ticket.") from exc

    @app.post(
        "/tickets/{ticket_id}/messages",
        status_code=status.HTTP_202_ACCEPTED,
        response_model=TicketMessage,
    )
    async def send_message(
        ticket_id: str,
        payload: MessageCreate,
        author_id: Annotated[str, Header(alias="X-Authenticated-User")],
    ) -> TicketMessage:
        message = TicketMessage(
            id=str(uuid4()),
            ticket_id=ticket_id,
            author_id=author_id,
            body=payload.body,
            created_at=datetime.now(timezone.utc),
        )
        try:
            await get_participant_ticket(ticket_id, author_id)
            return await repository.append_message(message)
        except TicketNotFound as exc:
            raise HTTPException(status_code=404, detail="Ticket not found.") from exc
        except TicketForbidden as exc:
            raise HTTPException(status_code=403, detail="Not authorized for this ticket.") from exc
        except SupportUnavailable as exc:
            raise HTTPException(status_code=503, detail="Support service unavailable.") from exc

    @app.patch("/tickets/{ticket_id}/status", response_model=Ticket)
    async def update_ticket_status(
        ticket_id: str,
        new_status: TicketStatus,
        principal_id: Annotated[str, Header(alias="X-Authenticated-User")],
    ) -> Ticket:
        try:
            ticket = await get_participant_ticket(ticket_id, principal_id)
            if ticket.assigned_agent_id != principal_id:
                raise TicketForbidden(ticket_id)
            return await repository.change_status(ticket_id, new_status)
        except TicketNotFound as exc:
            raise HTTPException(status_code=404, detail="Ticket not found.") from exc
        except TicketForbidden as exc:
            raise HTTPException(status_code=403, detail="Not authorized for this ticket.") from exc
        except SupportUnavailable as exc:
            raise HTTPException(status_code=503, detail="Support service unavailable.") from exc

    @app.websocket("/ws/tickets/{ticket_id}")
    async def ticket_websocket(websocket: WebSocket, ticket_id: str) -> None:
        try:
            principal = await websocket_authenticator.authenticate(websocket)
        except AuthenticationError:
            await websocket.close(code=4401)
            return

        ticket = await repository.get_ticket(ticket_id)
        if ticket is None:
            await websocket.close(code=4404)
            return
        if principal.id not in {ticket.client_id, ticket.assigned_agent_id}:
            await websocket.close(code=4403)
            return

        await hub.connect(ticket_id, websocket)
        try:
            while True:
                payload = MessageCreate.model_validate_json(await websocket.receive_text())
                message = TicketMessage(
                    id=str(uuid4()),
                    ticket_id=ticket_id,
                    author_id=principal.id,
                    body=payload.body,
                    created_at=datetime.now(timezone.utc),
                )
                await repository.append_message(message)
        except WebSocketDisconnect:
            await hub.disconnect(ticket_id, websocket)
        except Exception:
            await hub.disconnect(ticket_id, websocket)
            raise

    return app


class DemoWebSocketAuthenticator:
    """Only for local demonstrations; replace with the application's token verifier."""

    async def authenticate(self, websocket: WebSocket) -> Principal:
        principal_id = websocket.query_params.get("participant")
        if not principal_id:
            raise AuthenticationError("Missing authenticated participant.")
        return Principal(id=principal_id)


demo_repository = InMemorySupportRepository()
demo_hub = RealtimeHub()
app = create_app(demo_repository, demo_hub, DemoWebSocketAuthenticator())
