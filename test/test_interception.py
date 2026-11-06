import asyncio

import httpx
from starlette.requests import Request

import api_gateway


def test_gateway_filters_spoofed_identity_and_forwards_authenticated_context(monkeypatch):
    captured = {}

    class CapturingAsyncClient:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return None

        async def request(self, **kwargs):
            captured.update(kwargs)
            return httpx.Response(
                202,
                json={"accepted": True},
                headers={"x-service": "orders", "content-encoding": "identity"},
            )

    monkeypatch.setattr(api_gateway, "create_http_client", CapturingAsyncClient)
    body = b'{"user_id":7,"item_name":"Test","amount":2}'
    scope = {
        "type": "http",
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/orders",
        "raw_path": b"/orders",
        "query_string": b"tag=first&tag=second",
        "root_path": "",
        "headers": [
            (b"host", b"gateway.example.test"),
            (b"content-type", b"application/json"),
            (b"authorization", b"Bearer never-forward-this"),
            (b"x-authenticated-user", b"attacker"),
            (b"x-correlation-id", b"trace-123"),
            (b"connection", b"keep-alive"),
        ],
        "server": ("gateway.example.test", 8000),
        "client": ("127.0.0.1", 50000),
    }

    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    request = Request(scope, receive)
    response = asyncio.run(
        api_gateway.forward_request(
            request,
            "http://orders.internal/orders",
            authenticated_user="trusted-user",
        )
    )

    forwarded_headers = {key.lower(): value for key, value in captured["headers"].items()}
    assert captured["url"] == "http://orders.internal/orders"
    assert captured["method"] == "POST"
    assert captured["params"] == [("tag", "first"), ("tag", "second")]
    assert captured["content"] == body
    assert forwarded_headers["x-authenticated-user"] == "trusted-user"
    assert "authorization" not in forwarded_headers
    assert "connection" not in forwarded_headers
    assert forwarded_headers["x-correlation-id"] == "trace-123"
    assert response.status_code == 202
    assert response.headers["x-service"] == "orders"
    assert "content-encoding" not in response.headers
