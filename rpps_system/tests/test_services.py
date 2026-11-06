from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import jwt
import pytest
from fastapi.testclient import TestClient

import analytics_service
import api_gateway
import auth_service
import core_service
import validation_service


def valid_cpf() -> str:
    digits = "123456789"
    checks = []
    for length in (9, 10):
        total = sum(
            int(digits[index]) * (length + 1 - index)
            for index in range(length)
        )
        check_digit = (total * 10) % 11
        checks.append("0" if check_digit == 10 else str(check_digit))
        digits += checks[-1]
    return digits


def valid_insured_payload(cpf: str | None = None) -> dict[str, Any]:
    return {
        "full_name": "Pessoa de Teste",
        "cpf": cpf or valid_cpf(),
        "date_of_birth": "1980-04-03",
        "email": "teste@example.invalid",
        "registration_number": "TEST-001",
        "position": "Analista",
        "department": "Unidade de Teste",
        "admission_date": "2010-02-01",
    }


def test_password_hash_verification_and_malformed_hash() -> None:
    encoded = auth_service.hash_password("sample-password")

    assert auth_service.verify_password("sample-password", encoded)
    assert not auth_service.verify_password("wrong-password", encoded)
    assert not auth_service.verify_password("sample-password", "not-a-hash")


def test_login_issues_token_and_rejects_bad_credentials(
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RPPS_ADMIN_USERNAME", "operator")
    monkeypatch.setenv("RPPS_ADMIN_PASSWORD", "sample-password")
    monkeypatch.setenv("RPPS_JWT_SECRET", "a" * 40)
    app = auth_service.create_app(str(tmp_path / "auth.db"))

    with TestClient(app) as client:
        response = client.post(
            "/login",
            json={"username": "operator", "password": "sample-password"},
        )
        rejected = client.post(
            "/login",
            json={"username": "operator", "password": "wrong-password"},
        )

    assert response.status_code == 200
    claims = jwt.decode(
        response.json()["access_token"],
        "a" * 40,
        algorithms=["HS256"],
        audience=auth_service.JWT_AUDIENCE,
        issuer=auth_service.JWT_ISSUER,
    )
    assert claims["sub"] == "operator"
    assert rejected.status_code == 401


def test_login_fails_when_jwt_secret_is_not_configured(
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RPPS_ADMIN_USERNAME", "operator")
    monkeypatch.setenv("RPPS_ADMIN_PASSWORD", "sample-password")
    monkeypatch.setenv("RPPS_JWT_SECRET", "short")
    app = auth_service.create_app(str(tmp_path / "auth.db"))

    with TestClient(app) as client:
        response = client.post(
            "/login",
            json={"username": "operator", "password": "sample-password"},
        )

    assert response.status_code == 503


def test_core_registers_lists_and_rejects_duplicate_cpf(tmp_path: Any) -> None:
    app = core_service.create_app(str(tmp_path / "core.db"))
    payload = valid_insured_payload()

    with TestClient(app) as client:
        created = client.post(
            "/insured",
            json=payload,
            headers={"X-Actor-Id": "operator"},
        )
        duplicate = client.post(
            "/insured",
            json=payload,
            headers={"X-Actor-Id": "operator"},
        )
        listed = client.get("/insured")
        stats = client.get("/insured/stats")
        missing = client.get("/insured/not-found")

    assert created.status_code == 201
    assert created.json()["created_by"] == "operator"
    assert duplicate.status_code == 409
    assert listed.json()["total"] == 1
    assert stats.json()["total"] == 1
    assert missing.status_code == 404


def test_validation_flags_issues_and_enforces_single_review(tmp_path: Any) -> None:
    app = validation_service.create_app(str(tmp_path / "validation.db"))
    actor = {"X-Actor-Id": "operator"}

    with TestClient(app) as client:
        invalid_payload = {"id": "insured-pending", "full_name": "Pessoa Pendente", "cpf": "invalid"}
        pending = client.post("/cases", json=invalid_payload, headers=actor)
        case_id = pending.json()["id"]
        approved = client.patch(
            f"/cases/{case_id}",
            json={"decision": "approve", "note": "Dados conferidos"},
            headers=actor,
        )
        repeated = client.patch(
            f"/cases/{case_id}",
            json={"decision": "reject"},
            headers=actor,
        )
        missing = client.patch(
            "/cases/unknown",
            json={"decision": "approve"},
            headers=actor,
        )
        valid_payload = valid_insured_payload()
        valid_payload.update({"id": "insured-approved"})
        auto_approved = client.post("/cases", json=valid_payload, headers=actor)
        stats = client.get("/cases/stats")

    assert pending.status_code == 201
    assert pending.json()["status"] == "pending"
    assert pending.json()["issues"]
    assert approved.json()["status"] == "approved"
    assert approved.json()["reviewed_by"] == "operator"
    assert repeated.status_code == 409
    assert missing.status_code == 404
    assert auto_approved.json()["status"] == "approved"
    assert stats.json() == {"pending": 0, "approved": 2, "rejected": 0, "total": 2}


def test_cpf_and_required_field_validation() -> None:
    assert validation_service.valid_cpf(valid_cpf())
    assert not validation_service.valid_cpf("11111111111")
    assert not validation_service.valid_cpf("invalid")
    assert validation_service.find_issues({"cpf": "invalid"})


def test_gateway_requires_token_and_forwards_authenticated_actor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RPPS_JWT_SECRET", "b" * 40)
    token = jwt.encode(
        {
            "sub": "operator",
            "iss": api_gateway.JWT_ISSUER,
            "aud": api_gateway.JWT_AUDIENCE,
            "iat": datetime.now(timezone.utc),
            "exp": datetime.now(timezone.utc) + timedelta(minutes=5),
        },
        "b" * 40,
        algorithm=api_gateway.JWT_ALGORITHM,
    )

    class InternalResponse:
        status_code = 200
        is_error = False

        @staticmethod
        def json() -> dict[str, Any]:
            return {"total_insured": 3}

    class InternalClient:
        headers: dict[str, str] = {}

        async def request(self, method: str, url: str, **kwargs: Any) -> InternalResponse:
            self.headers = kwargs["headers"]
            return InternalResponse()

        async def aclose(self) -> None:
            return None

    with TestClient(api_gateway.app) as client:
        internal = InternalClient()
        client.app.state.http = internal
        unauthorized = client.get("/analytics/dashboard")
        authorized = client.get(
            "/analytics/dashboard",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert unauthorized.status_code == 401
    assert authorized.status_code == 200
    assert authorized.json() == {"total_insured": 3}
    assert internal.headers == {"X-Actor-Id": "operator"}


def test_analytics_retains_only_the_latest_twenty_snapshots(
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class InternalResponse:
        is_error = False

        def __init__(self, payload: dict[str, Any]) -> None:
            self.payload = payload

        def json(self) -> dict[str, Any]:
            return self.payload

    class InternalClient:
        def __init__(self, **_: Any) -> None:
            pass

        async def __aenter__(self) -> InternalClient:
            return self

        async def __aexit__(self, *_: Any) -> None:
            return None

        async def get(self, url: str) -> InternalResponse:
            if url.endswith("/cases/stats"):
                return InternalResponse(
                    {"pending": 0, "approved": 1, "rejected": 0, "total": 1}
                )
            return InternalResponse(
                {
                    "total": 1,
                    "registered_today": 1,
                    "registrations_by_day": [],
                }
            )

    monkeypatch.setattr(analytics_service.httpx, "AsyncClient", InternalClient)
    app = analytics_service.create_app(str(tmp_path / "analytics.db"))

    with TestClient(app) as client:
        responses = [client.get("/dashboard") for _ in range(25)]

    assert all(response.status_code == 200 for response in responses)
    assert len(responses[-1].json()["snapshots"]) == 20
