from __future__ import annotations

import os
from contextlib import asynccontextmanager
from typing import Annotated, Any

import httpx
import jwt
from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field

AUTH_URL = os.getenv("RPPS_AUTH_URL", "http://auth-service:8001").rstrip("/")
CORE_URL = os.getenv("RPPS_CORE_URL", "http://core-service:8002").rstrip("/")
VALIDATION_URL = os.getenv("RPPS_VALIDATION_URL", "http://validation-service:8003").rstrip("/")
ANALYTICS_URL = os.getenv("RPPS_ANALYTICS_URL", "http://analytics-service:8004").rstrip("/")
JWT_ISSUER = "rpps-auth-service"
JWT_AUDIENCE = "rpps-api"
JWT_ALGORITHM = "HS256"
BEARER = HTTPBearer(auto_error=False)


class ReviewDecision(BaseModel):
    decision: str = Field(pattern="^(approve|reject)$")
    note: str | None = Field(default=None, max_length=500)


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.http = httpx.AsyncClient(timeout=httpx.Timeout(10.0, connect=3.0))
    yield
    await app.state.http.aclose()


app = FastAPI(title="RPPS API Gateway", version="1.0.0", lifespan=lifespan)


async def current_user(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(BEARER)],
) -> str:
    secret = os.getenv("RPPS_JWT_SECRET", "")
    unauthorized = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Valid bearer token required.",
        headers={"WWW-Authenticate": "Bearer"},
    )
    if not secret or credentials is None or credentials.scheme.lower() != "bearer":
        raise unauthorized
    try:
        payload = jwt.decode(
            credentials.credentials,
            secret,
            algorithms=[JWT_ALGORITHM],
            issuer=JWT_ISSUER,
            audience=JWT_AUDIENCE,
            options={"require": ["exp", "iat", "iss", "sub", "aud"]},
        )
    except jwt.InvalidTokenError as exc:
        raise unauthorized from exc
    subject = payload.get("sub")
    if not isinstance(subject, str) or not subject:
        raise unauthorized
    return subject


async def send(
    request: Request,
    method: str,
    url: str,
    *,
    payload: Any = None,
    params: dict[str, Any] | None = None,
    actor: str | None = None,
) -> Any:
    headers = {"X-Actor-Id": actor} if actor else {}
    try:
        response = await request.app.state.http.request(
            method,
            url,
            json=payload,
            params=params,
            headers=headers,
        )
    except httpx.RequestError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="An internal service is temporarily unavailable.",
        ) from exc
    if response.is_error:
        try:
            detail = response.json().get("detail", "Internal service returned an error.")
        except ValueError:
            detail = "Internal service returned an error."
        raise HTTPException(status_code=response.status_code, detail=detail)
    if response.status_code == status.HTTP_204_NO_CONTENT:
        return None
    return response.json()


@app.get("/health")
async def health(request: Request) -> dict[str, str]:
    return {"service": "gateway", "status": "ok"}


@app.post("/login")
async def login(request: Request) -> Any:
    try:
        payload = await request.json()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="A JSON login body is required.") from exc
    return await send(request, "POST", f"{AUTH_URL}/login", payload=payload)


@app.get("/analytics/dashboard")
async def dashboard(
    request: Request,
    actor: Annotated[str, Depends(current_user)],
) -> Any:
    return await send(request, "GET", f"{ANALYTICS_URL}/dashboard", actor=actor)


@app.get("/analytics/report")
async def report(
    request: Request,
    actor: Annotated[str, Depends(current_user)],
    format: str = "json",
    days: int = 7,
) -> Any:
    allowed = {"json", "csv", "png"}
    if format not in allowed:
        raise HTTPException(status_code=400, detail="Report format must be one of: json, csv, png.")
    return await send(
        request,
        "GET",
        f"{ANALYTICS_URL}/report",
        params={"format": format, "days": days},
        actor=actor,
    )


@app.get("/insured")
async def list_insured(
    request: Request,
    actor: Annotated[str, Depends(current_user)],
    search: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> Any:
    return await send(
        request,
        "GET",
        f"{CORE_URL}/insured",
        params={"search": search, "limit": limit, "offset": offset},
        actor=actor,
    )


@app.get("/insured/{insured_id}")
async def get_insured(
    insured_id: str,
    request: Request,
    actor: Annotated[str, Depends(current_user)],
) -> Any:
    return await send(request, "GET", f"{CORE_URL}/insured/{insured_id}", actor=actor)


@app.get("/insured/stats")
async def insured_stats(
    request: Request,
    actor: Annotated[str, Depends(current_user)],
) -> Any:
    return await send(request, "GET", f"{CORE_URL}/insured/stats", actor=actor)


@app.post("/insured", status_code=status.HTTP_201_CREATED)
async def register_insured(
    request: Request,
    actor: Annotated[str, Depends(current_user)],
) -> Any:
    try:
        payload = await request.json()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="A JSON insured record is required.") from exc
    insured = await send(request, "POST", f"{CORE_URL}/insured", payload=payload, actor=actor)
    case = await send(
        request,
        "POST",
        f"{VALIDATION_URL}/cases",
        payload=insured,
        actor=actor,
    )
    return {"insured": insured, "validation_case": case}


@app.get("/pending")
async def list_pending(
    request: Request,
    actor: Annotated[str, Depends(current_user)],
    case_status: str = "pending",
    limit: int = 100,
) -> Any:
    return await send(
        request,
        "GET",
        f"{VALIDATION_URL}/cases",
        params={"status": case_status, "limit": limit},
        actor=actor,
    )


@app.patch("/pending/{case_id}")
async def decide_pending(
    case_id: str,
    decision: ReviewDecision,
    request: Request,
    actor: Annotated[str, Depends(current_user)],
) -> Any:
    return await send(
        request,
        "PATCH",
        f"{VALIDATION_URL}/cases/{case_id}",
        payload=decision.model_dump(exclude_none=True),
        actor=actor,
    )

