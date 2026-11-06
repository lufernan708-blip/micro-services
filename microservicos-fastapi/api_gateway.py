import hmac
import os
from datetime import datetime, timedelta, timezone
from typing import Annotated

import httpx
import jwt
from fastapi import Depends, FastAPI, HTTPException, Request, Response, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field

app = FastAPI(title="API Gateway", version="1.0.0")

USER_SERVICE_URL = os.getenv("USER_SERVICE_URL", "http://127.0.0.1:8001").rstrip("/")
ORDER_SERVICE_URL = os.getenv("ORDER_SERVICE_URL", "http://127.0.0.1:8002").rstrip("/")
JWT_ISSUER = "microservicos-fastapi"
JWT_ALGORITHM = "HS256"
TOKEN_LIFETIME = timedelta(minutes=30)
HTTP_BEARER = HTTPBearer(auto_error=False)
HOP_BY_HOP_HEADERS = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
}


def create_http_client(**kwargs) -> httpx.AsyncClient:
    return httpx.AsyncClient(**kwargs)


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=1, max_length=256)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int


def get_jwt_secret() -> str:
    secret = os.getenv("JWT_SECRET_KEY", "")
    if len(secret.encode("utf-8")) < 32:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="JWT_SECRET_KEY must contain at least 32 bytes.",
        )
    return secret


@app.post("/login", response_model=TokenResponse)
async def login(credentials: LoginRequest) -> TokenResponse:
    configured_username = os.getenv("LOGIN_USERNAME", "")
    configured_password = os.getenv("LOGIN_PASSWORD", "")
    if not configured_username or not configured_password:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Login credentials are not configured.",
        )

    username_matches = hmac.compare_digest(
        credentials.username.encode("utf-8"),
        configured_username.encode("utf-8"),
    )
    password_matches = hmac.compare_digest(
        credentials.password.encode("utf-8"),
        configured_password.encode("utf-8"),
    )
    if not username_matches or not password_matches:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    now = datetime.now(timezone.utc)
    token = jwt.encode(
        {
            "sub": configured_username,
            "iss": JWT_ISSUER,
            "iat": now,
            "exp": now + TOKEN_LIFETIME,
        },
        get_jwt_secret(),
        algorithm=JWT_ALGORITHM,
    )
    return TokenResponse(access_token=token, expires_in=int(TOKEN_LIFETIME.total_seconds()))


async def authenticate_request(
    credentials: Annotated[
        HTTPAuthorizationCredentials | None,
        Depends(HTTP_BEARER),
    ],
) -> str:
    unauthorized = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Valid bearer token required.",
        headers={"WWW-Authenticate": "Bearer"},
    )
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise unauthorized

    try:
        payload = jwt.decode(
            credentials.credentials,
            get_jwt_secret(),
            algorithms=[JWT_ALGORITHM],
            issuer=JWT_ISSUER,
            options={"require": ["exp", "iat", "iss", "sub"]},
        )
    except (jwt.InvalidTokenError, HTTPException) as exc:
        if isinstance(exc, HTTPException):
            raise
        raise unauthorized from exc

    subject = payload.get("sub")
    if not isinstance(subject, str) or not subject:
        raise unauthorized
    return subject


@app.api_route("/users/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
async def proxy_users(request: Request, path: str):
    return await forward_request(request, f"{USER_SERVICE_URL}/users/{path}")


@app.api_route("/users", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
async def proxy_users_root(request: Request):
    return await forward_request(request, f"{USER_SERVICE_URL}/users")


@app.api_route("/orders/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
async def proxy_orders(
    request: Request,
    path: str,
    authenticated_user: Annotated[str, Depends(authenticate_request)],
):
    return await forward_request(
        request,
        f"{ORDER_SERVICE_URL}/orders/{path}",
        authenticated_user=authenticated_user,
    )


@app.api_route("/orders", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
async def proxy_orders_root(
    request: Request,
    authenticated_user: Annotated[str, Depends(authenticate_request)],
):
    return await forward_request(
        request,
        f"{ORDER_SERVICE_URL}/orders",
        authenticated_user=authenticated_user,
    )


async def forward_request(
    request: Request,
    target_url: str,
    authenticated_user: str | None = None,
) -> Response:
    headers = {
        key: value
        for key, value in request.headers.items()
        if key.lower()
        not in HOP_BY_HOP_HEADERS
        | {"host", "content-length", "authorization", "x-authenticated-user"}
    }
    if authenticated_user is not None:
        headers["X-Authenticated-User"] = authenticated_user

    try:
        async with create_http_client(timeout=10.0) as client:
            downstream_response = await client.request(
                method=request.method,
                url=target_url,
                params=list(request.query_params.multi_items()),
                content=await request.body(),
                headers=headers,
            )
    except httpx.RequestError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Microsserviço de destino indisponível.",
        ) from exc

    response_headers = {
        key: value
        for key, value in downstream_response.headers.items()
        if key.lower()
        not in HOP_BY_HOP_HEADERS | {"content-length", "content-encoding"}
    }
    return Response(
        content=downstream_response.content,
        status_code=downstream_response.status_code,
        headers=response_headers,
    )
