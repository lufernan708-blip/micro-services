from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from customer_support.support_service import (
    AgentRegistry,
    AuthenticationError,
    EventPublisher,
    InMemorySupportRepository,
    OutboxDispatcher,
    Principal,
    RabbitMQEventBus,
    RealtimeEventConsumer,
    RealtimeHub,
    RoutingEventWorker,
    SupportRepository,
    SupportUnavailable,
    Ticket,
    TicketEvent,
    TicketPriority,
    TicketRoutingWorker,
    TicketStatus,
    create_app,
)


@pytest.fixture
def repository():
    return InMemorySupportRepository()


@pytest.fixture
def hub():
    return RealtimeHub()


@pytest.fixture
def authenticator():
    return AsyncMock()


@pytest.fixture
def client(repository, hub, authenticator):
    return TestClient(create_app(repository, hub, authenticator))


@pytest.fixture
def ticket_payload():
    return {
        "subject": "Acesso bloqueado",
        "description": "Não consigo entrar na minha conta.",
        "category": "account",
        "priority": "high",
        "required_skills": ["portuguese", "account"],
        "attachment_ids": ["opaque-upload-reference"],
    }


def test_open_ticket_is_accepted_and_event_is_queued(client, repository, ticket_payload):
    response = client.post(
        "/tickets",
        headers={"X-Authenticated-User": "client-17"},
        json=ticket_payload,
    )

    assert response.status_code == 202
    ticket = response.json()
    assert ticket["client_id"] == "client-17"
    assert ticket["priority"] == TicketPriority.HIGH
    assert ticket["status"] == TicketStatus.QUEUED
    assert ticket["attachment_ids"] == ["opaque-upload-reference"]
    assert repository.events[0].event_type == "ticket.opened"
    assert repository.events[0].ticket_id == ticket["id"]


def test_open_ticket_surfaces_repository_failure(ticket_payload):
    repository = AsyncMock(spec=SupportRepository)
    repository.open_ticket.side_effect = SupportUnavailable()
    client = TestClient(create_app(repository, RealtimeHub(), AsyncMock()))

    response = client.post(
        "/tickets",
        headers={"X-Authenticated-User": "client-17"},
        json=ticket_payload,
    )

    assert response.status_code == 503
    repository.open_ticket.assert_awaited_once()


def test_message_history_and_missing_ticket(client, repository, ticket_payload):
    ticket_response = client.post(
        "/tickets",
        headers={"X-Authenticated-User": "client-17"},
        json=ticket_payload,
    )
    ticket_id = ticket_response.json()["id"]

    created = client.post(
        f"/tickets/{ticket_id}/messages",
        headers={"X-Authenticated-User": "client-17"},
        json={"body": "Já tentei redefinir a senha."},
    )
    history = client.get(
        f"/tickets/{ticket_id}/messages",
        headers={"X-Authenticated-User": "client-17"},
    )
    missing = client.get(
        "/tickets/not-a-ticket/messages",
        headers={"X-Authenticated-User": "client-17"},
    )

    assert created.status_code == 202
    assert created.json()["author_id"] == "client-17"
    assert [message["body"] for message in history.json()] == [
        "Já tentei redefinir a senha."
    ]
    assert missing.status_code == 404
    assert [event.event_type for event in repository.events] == [
        "ticket.opened",
        "message.created",
    ]


@pytest.mark.asyncio
async def test_status_update_writes_event(client, repository, ticket_payload):
    created = client.post(
        "/tickets",
        headers={"X-Authenticated-User": "client-17"},
        json=ticket_payload,
    )
    ticket_id = created.json()["id"]
    assert await repository.assign_if_queued(ticket_id, "agent-2")

    updated = client.patch(
        f"/tickets/{ticket_id}/status",
        headers={"X-Authenticated-User": "agent-2"},
        params={"new_status": "in_progress"},
    )

    assert updated.status_code == 200
    assert updated.json()["status"] == "in_progress"
    assert repository.events[-1].event_type == "ticket.status_changed"


def test_ticket_messages_and_status_are_limited_to_participants(
    client,
    ticket_payload,
):
    created = client.post(
        "/tickets",
        headers={"X-Authenticated-User": "client-17"},
        json=ticket_payload,
    )
    ticket_id = created.json()["id"]
    other_user = {"X-Authenticated-User": "client-18"}

    history = client.get(
        f"/tickets/{ticket_id}/messages",
        headers=other_user,
    )
    message = client.post(
        f"/tickets/{ticket_id}/messages",
        headers=other_user,
        json={"body": "Unauthorized message"},
    )
    status_update = client.patch(
        f"/tickets/{ticket_id}/status",
        headers=other_user,
        params={"new_status": "resolved"},
    )

    assert history.status_code == 403
    assert message.status_code == 403
    assert status_update.status_code == 403


@pytest.mark.asyncio
async def test_router_claims_skill_matched_agent_and_assigns_atomically(repository):
    ticket = Ticket(
        id="ticket-1",
        client_id="client-1",
        subject="Billing issue",
        description="Need help with an invoice.",
        category="billing",
        priority=TicketPriority.HIGH,
        required_skills={"billing", "english"},
        attachment_ids=[],
        created_at=datetime.now(timezone.utc),
    )
    await repository.open_ticket(ticket)
    agents = AsyncMock(spec=AgentRegistry)
    agents.claim_least_active.return_value = "agent-3"

    assigned = await TicketRoutingWorker(repository, agents).route(ticket.id)

    assert assigned == "agent-3"
    agents.claim_least_active.assert_awaited_once_with({"billing", "english"})
    stored_ticket = await repository.get_ticket(ticket.id)
    assert stored_ticket is not None
    assert stored_ticket.assigned_agent_id == "agent-3"
    assert stored_ticket.status == TicketStatus.ASSIGNED
    assert repository.events[-1].event_type == "ticket.assigned"


@pytest.mark.asyncio
async def test_router_leaves_ticket_queued_when_no_agent_matches(repository):
    ticket = Ticket(
        id="ticket-2",
        client_id="client-1",
        subject="Refund",
        description="Please review my refund.",
        category="billing",
        priority=TicketPriority.NORMAL,
        required_skills={"billing"},
        attachment_ids=[],
        created_at=datetime.now(timezone.utc),
    )
    await repository.open_ticket(ticket)
    agents = AsyncMock(spec=AgentRegistry)
    agents.claim_least_active.return_value = None

    assigned = await TicketRoutingWorker(repository, agents).route(ticket.id)

    assert assigned is None
    stored_ticket = await repository.get_ticket(ticket.id)
    assert stored_ticket is not None
    assert stored_ticket.status == TicketStatus.QUEUED
    assert repository.events[-1].event_type == "ticket.waiting"
    agents.release_claim.assert_not_awaited()


@pytest.mark.asyncio
async def test_router_releases_claim_if_concurrent_assignment_wins():
    repository = AsyncMock(spec=SupportRepository)
    repository.get_ticket.return_value = Ticket(
        id="ticket-race",
        client_id="client-1",
        subject="Concurrent",
        description="Race test.",
        category="general",
        priority=TicketPriority.NORMAL,
        required_skills={"general"},
        attachment_ids=[],
        created_at=datetime.now(timezone.utc),
    )
    repository.assign_if_queued.return_value = False
    agents = AsyncMock(spec=AgentRegistry)
    agents.claim_least_active.return_value = "agent-4"
    repository.get_ticket.side_effect = [
        repository.get_ticket.return_value,
        repository.get_ticket.return_value.model_copy(
            update={"status": TicketStatus.ASSIGNED, "assigned_agent_id": "agent-2"}
        ),
    ]

    assigned = await TicketRoutingWorker(repository, agents).route("ticket-race")

    assert assigned == "agent-2"
    agents.release_claim.assert_awaited_once_with("agent-4")


@pytest.mark.asyncio
async def test_router_releases_claim_and_reraises_repository_failure():
    repository = AsyncMock(spec=SupportRepository)
    repository.get_ticket.return_value = Ticket(
        id="ticket-failure",
        client_id="client-1",
        subject="Failure",
        description="Repository failure.",
        category="general",
        priority=TicketPriority.NORMAL,
        required_skills=set(),
        attachment_ids=[],
        created_at=datetime.now(timezone.utc),
    )
    repository.assign_if_queued.side_effect = RuntimeError("assignment store unavailable")
    agents = AsyncMock(spec=AgentRegistry)
    agents.claim_least_active.return_value = "agent-5"

    with pytest.raises(RuntimeError, match="assignment store unavailable"):
        await TicketRoutingWorker(repository, agents).route("ticket-failure")

    agents.release_claim.assert_awaited_once_with("agent-5")


@pytest.mark.asyncio
async def test_outbox_dispatch_marks_only_confirmed_events(repository):
    ticket = Ticket(
        id="ticket-outbox",
        client_id="client-1",
        subject="Dispatch",
        description="Outbox dispatch.",
        category="general",
        priority=TicketPriority.NORMAL,
        required_skills=set(),
        attachment_ids=[],
        created_at=datetime.now(timezone.utc),
    )
    await repository.open_ticket(ticket)
    publisher = AsyncMock(spec=EventPublisher)

    dispatched = await OutboxDispatcher(repository, publisher).dispatch_once()

    assert dispatched == 1
    publisher.publish.assert_awaited_once_with(repository.events[0])
    assert await repository.pending_events() == []


@pytest.mark.asyncio
async def test_outbox_publish_failure_preserves_event_for_retry(repository):
    ticket = Ticket(
        id="ticket-retry",
        client_id="client-1",
        subject="Retry",
        description="Publish failure.",
        category="general",
        priority=TicketPriority.NORMAL,
        required_skills=set(),
        attachment_ids=[],
        created_at=datetime.now(timezone.utc),
    )
    await repository.open_ticket(ticket)
    publisher = AsyncMock(spec=EventPublisher)
    publisher.publish.side_effect = RuntimeError("broker unavailable")

    with pytest.raises(RuntimeError, match="broker unavailable"):
        await OutboxDispatcher(repository, publisher).dispatch_once()

    assert len(await repository.pending_events()) == 1


@pytest.mark.asyncio
async def test_rabbitmq_adapter_publishes_persistent_confirmed_event():
    connection = AsyncMock()
    connection.is_closed = False
    channel = AsyncMock()
    exchange = AsyncMock()
    connection.channel.return_value = channel
    channel.declare_exchange.return_value = exchange

    with patch(
        "customer_support.support_service.aio_pika.connect_robust",
        new_callable=AsyncMock,
        return_value=connection,
    ) as connect:
        bus = RabbitMQEventBus("injected-test-value")
        event = repository_event("ticket-queue")
        await bus.publish(event)

    connect.assert_awaited_once_with("injected-test-value")
    channel.declare_exchange.assert_awaited_once()
    published_message = exchange.publish.await_args.args[0]
    assert published_message.message_id == event.event_id
    assert published_message.delivery_mode.value == 2
    assert exchange.publish.await_args.kwargs["routing_key"] == event.event_type
    assert exchange.publish.await_args.kwargs["mandatory"] is True


@pytest.mark.asyncio
async def test_realtime_consumer_broadcasts_event_to_ticket_room(hub):
    hub.broadcast = AsyncMock()
    event = repository_event("ticket-live")

    await RealtimeEventConsumer(hub).handle(event)

    hub.broadcast.assert_awaited_once()
    room_id, payload = hub.broadcast.await_args.args
    assert room_id == "ticket-live"
    assert payload["type"] == "ticket.opened"
    assert payload["event_id"] == event.event_id


@pytest.mark.asyncio
async def test_realtime_hub_prunes_failed_connections():
    hub = RealtimeHub()
    healthy = AsyncMock()
    disconnected = AsyncMock()
    disconnected.send_json.side_effect = RuntimeError("socket closed")
    hub._connections["ticket-live"].update({healthy, disconnected})

    await hub.broadcast("ticket-live", {"type": "ticket.updated"})

    healthy.send_json.assert_awaited_once_with({"type": "ticket.updated"})
    assert hub._connections["ticket-live"] == {healthy}


def test_authenticated_websocket_accepts_messages_into_event_outbox(
    client,
    repository,
    authenticator,
    ticket_payload,
):
    created = client.post(
        "/tickets",
        headers={"X-Authenticated-User": "client-17"},
        json=ticket_payload,
    )
    ticket_id = created.json()["id"]
    authenticator.authenticate.return_value = Principal(id="client-17")

    with client.websocket_connect(f"/ws/tickets/{ticket_id}") as websocket:
        websocket.send_json({"body": "Estou conectado pelo chat."})
        websocket.close()

    assert (repository.messages[ticket_id][0].author_id) == "client-17"
    assert repository.events[-1].event_type == "message.created"


def test_websocket_rejects_missing_authentication(client, authenticator, ticket_payload):
    created = client.post(
        "/tickets",
        headers={"X-Authenticated-User": "client-17"},
        json=ticket_payload,
    )
    authenticator.authenticate.side_effect = AuthenticationError("Missing identity.")

    with pytest.raises(WebSocketDisconnect) as error:
        with client.websocket_connect(f"/ws/tickets/{created.json()['id']}"):
            pass

    assert error.value.code == 4401


def repository_event(ticket_id):
    return TicketEvent(
        event_id=f"event-{ticket_id}",
        event_type="ticket.opened",
        occurred_at=datetime.now(timezone.utc),
        ticket_id=ticket_id,
        payload={"ticket": {"id": ticket_id}},
    )
