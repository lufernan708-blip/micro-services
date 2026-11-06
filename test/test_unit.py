from fastapi.testclient import TestClient

import user_service


def test_user_create_and_read():
    with TestClient(user_service.app) as client:
        created = client.post(
            "/users",
            json={"name": "Luiz", "email": "luiz@example.test"},
        )

        assert created.status_code == 201
        user = created.json()
        assert user["id"] > 0
        assert user["name"] == "Luiz"

        fetched = client.get(f"/users/{user['id']}")
        assert fetched.status_code == 200
        assert fetched.json() == user


def test_duplicate_user_email_is_rejected():
    with TestClient(user_service.app) as client:
        payload = {"name": "Ana", "email": "ana@example.test"}
        assert client.post("/users", json=payload).status_code == 201

        duplicate = client.post("/users", json=payload)

        assert duplicate.status_code == 400
        assert duplicate.json()["detail"] == "E-mail já cadastrado."


def test_missing_user_returns_not_found():
    with TestClient(user_service.app) as client:
        response = client.get("/users/987654321")

    assert response.status_code == 404
