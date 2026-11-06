import asyncio
import json
import sqlite3
from contextlib import closing

import httpx
import jwt
import pytest
from fastapi import HTTPException
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

import api_gateway
import order_service

TEST_SECRET = "test-secret-key-with-at-least-32-bytes"


def test_login_rejects_bad_credentials_and_issues_signed_token(monkeypatch):
    monkeypatch.setenv("LOGIN_USERNAME", "tester")
    monkeypatch.setenv("LOGIN_PASSWORD", "correct-horse")
    monkeypatch.setenv("JWT_SECRET_KEY", TEST_SECRET)

    with TestClient(api_gateway.app) as client:
        denied = client.post(
            "/login",
            json={"username": "tester", "password": "wrong"},
        )
        accepted = client.post(
            "/login",
            json={"username": "tester", "password": "correct-horse"},
        )

    assert denied.status_code == 401
    assert accepted.status_code == 200
    token = accepted.json()["access_token"]
    claims = jwt.decode(
        token,
        TEST_SECRET,
        algorithms=["HS256"],
        issuer=api_gateway.JWT_ISSUER,
    )
    assert claims["sub"] == "tester"


def test_orders_require_a_valid_bearer_token(monkeypatch):
    monkeypatch.setenv("LOGIN_USERNAME", "tester")
    monkeypatch.setenv("LOGIN_PASSWORD", "correct-horse")
    monkeypatch.setenv("JWT_SECRET_KEY", TEST_SECRET)

    async def fake_forward(request, target_url, authenticated_user=None):
        return JSONResponse({"forwarded_user": authenticated_user}, status_code=201)

    monkeypatch.setattr(api_gateway, "forward_request", fake_forward)
    with TestClient(api_gateway.app) as client:
        missing_token = client.post("/orders", json={"user_id": 1})
        forged_identity = client.post(
            "/orders",
            json={"user_id": 1},
            headers={"X-Authenticated-User": "attacker"},
        )
        token = client.post(
            "/login",
            json={"username": "tester", "password": "correct-horse"},
        ).json()["access_token"]
        allowed = client.post(
            "/orders",
            json={"user_id": 1},
            headers={"Authorization": f"Bearer {token}"},
        )

    assert missing_token.status_code == 401
    assert forged_identity.status_code == 401
    assert allowed.status_code == 201
    assert allowed.json()["forwarded_user"] == "tester"


def test_order_and_event_are_written_atomically(monkeypatch):
    class UserServiceClient:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return None

        async def get(self, url):
            assert url.endswith("/users/42")
            return httpx.Response(200, json={"id": 42})

    monkeypatch.setattr(order_service, "create_http_client", UserServiceClient)
    result = asyncio.run(
        order_service.create_order(
            order_service.OrderCreate(
                user_id=42,
                item_name="Regression keyboard",
                amount=250,
            ),
            authenticated_user="tester",
        )
    )

    assert result["user_id"] == 42
    with closing(sqlite3.connect(order_service.DATABASE_PATH)) as connection:
        event_row = connection.execute(
            "SELECT event_type, payload FROM outbox_events"
        ).fetchone()
        order_count = connection.execute("SELECT COUNT(*) FROM orders").fetchone()[0]

    assert order_count == 1
    assert event_row is not None
    assert event_row[0] == "order.created"
    event = json.loads(event_row[1])
    assert event["order"]["id"] == result["id"]
    assert event["created_by"] == "tester"


def test_order_is_not_saved_when_user_does_not_exist(monkeypatch):
    class UserServiceClient:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return None

        async def get(self, _url):
            return httpx.Response(404)

    monkeypatch.setattr(order_service, "create_http_client", UserServiceClient)
    with pytest.raises(HTTPException) as exception:
        asyncio.run(
            order_service.create_order(
                order_service.OrderCreate(
                    user_id=404,
                    item_name="No user",
                    amount=1,
                ),
                authenticated_user="tester",
            )
        )

    assert exception.value.status_code == 404
    with closing(sqlite3.connect(order_service.DATABASE_PATH)) as connection:
        assert connection.execute("SELECT COUNT(*) FROM orders").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM outbox_events").fetchone()[0] == 0
