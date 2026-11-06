from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import sqlite3
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Annotated

import jwt
from fastapi import FastAPI, HTTPException, status
from pydantic import BaseModel, Field

from common import connection, database_path

JWT_ISSUER = "rpps-auth-service"
JWT_AUDIENCE = "rpps-api"
JWT_ALGORITHM = "HS256"
TOKEN_LIFETIME = timedelta(minutes=30)
PASSWORD_SCRYPT_N = 2**14


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=1, max_length=256)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int


def hash_password(password: str, salt: bytes | None = None) -> str:
    actual_salt = salt or secrets.token_bytes(16)
    digest = hashlib.scrypt(
        password.encode("utf-8"),
        salt=actual_salt,
        n=PASSWORD_SCRYPT_N,
        r=8,
        p=1,
        dklen=32,
    )
    return f"{actual_salt.hex()}:{digest.hex()}"


def verify_password(password: str, encoded_hash: str) -> bool:
    try:
        salt_hex, expected_hex = encoded_hash.split(":", maxsplit=1)
        actual = hash_password(password, bytes.fromhex(salt_hex)).split(":", maxsplit=1)[1]
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(actual, expected_hex)


def create_app(db_path: str | None = None) -> FastAPI:
    path = db_path or database_path("AUTH_DB_PATH", "auth.db")

    def initialize() -> None:
        with connection(path) as db:
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS accounts (
                    username TEXT PRIMARY KEY,
                    password_hash TEXT NOT NULL,
                    active INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL
                )
                """
            )
            username = os.getenv("RPPS_ADMIN_USERNAME", "")
            password = os.getenv("RPPS_ADMIN_PASSWORD", "")
            if username and password:
                db.execute(
                    """
                    INSERT OR IGNORE INTO accounts
                        (username, password_hash, active, created_at)
                    VALUES (?, ?, 1, ?)
                    """,
                    (username, hash_password(password), datetime.now(timezone.utc).isoformat()),
                )

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        initialize()
        yield

    app = FastAPI(title="RPPS Auth Service", version="1.0.0", lifespan=lifespan)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"service": "auth", "status": "ok"}

    @app.post("/login", response_model=TokenResponse)
    async def login(credentials: LoginRequest) -> TokenResponse:
        secret = os.getenv("RPPS_JWT_SECRET", "")
        if len(secret.encode("utf-8")) < 32:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Authentication is not configured.",
            )

        with connection(path) as db:
            account = db.execute(
                "SELECT username, password_hash, active FROM accounts WHERE username = ?",
                (credentials.username,),
            ).fetchone()

        password_matches = verify_password(
            credentials.password,
            account["password_hash"] if account else hash_password("invalid-account"),
        )
        if account is None or not account["active"] or not password_matches:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid username or password.",
                headers={"WWW-Authenticate": "Bearer"},
            )

        now = datetime.now(timezone.utc)
        token = jwt.encode(
            {
                "sub": account["username"],
                "iss": JWT_ISSUER,
                "aud": JWT_AUDIENCE,
                "iat": now,
                "exp": now + TOKEN_LIFETIME,
            },
            secret,
            algorithm=JWT_ALGORITHM,
        )
        return TokenResponse(access_token=token, expires_in=int(TOKEN_LIFETIME.total_seconds()))

    return app


app = create_app()
