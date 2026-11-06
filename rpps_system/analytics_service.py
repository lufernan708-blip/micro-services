from __future__ import annotations

import asyncio
import io
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, Literal
from uuid import uuid4

import httpx
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd
from fastapi import FastAPI, HTTPException, Query, Response, status

from common import connection, database_path

CORE_SERVICE_URL = os.getenv("RPPS_CORE_URL", "http://core-service:8002").rstrip("/")
VALIDATION_SERVICE_URL = os.getenv(
    "RPPS_VALIDATION_URL",
    "http://validation-service:8003",
).rstrip("/")


def create_app(db_path: str | None = None) -> FastAPI:
    path = db_path or database_path("RPPS_ANALYTICS_DB_PATH", "analytics.db")

    def initialize() -> None:
        with connection(path) as db:
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS dashboard_snapshots (
                    id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    total_insured INTEGER NOT NULL,
                    pending INTEGER NOT NULL,
                    approved INTEGER NOT NULL,
                    rejected INTEGER NOT NULL
                )
                """
            )

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        initialize()
        yield

    app = FastAPI(title="RPPS Analytics Service", version="1.0.0", lifespan=lifespan)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"service": "analytics", "status": "ok"}

    @app.get("/dashboard")
    async def dashboard() -> dict[str, Any]:
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                insured_response, validation_response = await asyncio.gather(
                    client.get(f"{CORE_SERVICE_URL}/insured/stats"),
                    client.get(f"{VALIDATION_SERVICE_URL}/cases/stats"),
                )
        except httpx.RequestError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="A dashboard data service is unavailable.",
            ) from exc

        if insured_response.is_error or validation_response.is_error:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="A dashboard data service returned an error.",
            )
        insured = insured_response.json()
        validation = validation_response.json()
        now = datetime.now(timezone.utc)
        with connection(path) as db:
            db.execute(
                """
                INSERT INTO dashboard_snapshots
                    (id, created_at, total_insured, pending, approved, rejected)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    str(uuid4()),
                    now.isoformat(),
                    insured["total"],
                    validation["pending"],
                    validation["approved"],
                    validation["rejected"],
                ),
            )
            db.execute(
                """
                DELETE FROM dashboard_snapshots
                WHERE id NOT IN (
                    SELECT id FROM dashboard_snapshots
                    ORDER BY created_at DESC, rowid DESC
                    LIMIT 20
                )
                """
            )
            snapshots = db.execute(
                """
                SELECT created_at, total_insured, pending, approved, rejected
                FROM dashboard_snapshots
                ORDER BY created_at DESC, rowid DESC
                LIMIT 20
                """
            ).fetchall()

        goal = int(os.getenv("RPPS_RECENSUS_GOAL", "0"))
        return {
            "updated_at": now.isoformat(),
            "goal": goal or None,
            "registered_today": insured["registered_today"],
            "total_insured": insured["total"],
            "registrations_by_day": insured["registrations_by_day"],
            "validation": validation,
            "progress_percent": round(min(insured["total"] / goal * 100, 100), 1) if goal > 0 else None,
            "snapshots": [dict(row) for row in reversed(snapshots)],
        }

    @app.get("/report")
    async def executive_report(
        format: Literal["json", "csv", "png"] = Query(default="json"),
        days: int = Query(default=7, ge=1, le=365),
    ) -> Response | dict[str, Any]:
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                insured_response, validation_response = await asyncio.gather(
                    client.get(f"{CORE_SERVICE_URL}/insured/stats"),
                    client.get(f"{VALIDATION_SERVICE_URL}/cases/stats"),
                )
        except httpx.RequestError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="A dashboard data service is unavailable.",
            ) from exc

        if insured_response.is_error or validation_response.is_error:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="A dashboard data service returned an error.",
            )

        insured_data = insured_response.json()
        validation_data = validation_response.json()
        daily_records = pd.DataFrame(insured_data.get("registrations_by_day", []))
        if not daily_records.empty:
            daily_records["day"] = pd.to_datetime(daily_records["day"])
            daily_records = daily_records.sort_values("day").tail(days)
        else:
            daily_records = pd.DataFrame({"day": pd.date_range(end=pd.Timestamp.utcnow(), periods=days, freq="D"), "count": 0})

        report = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "total_insured": insured_data.get("total", 0),
            "registered_today": insured_data.get("registered_today", 0),
            "pending_validation": validation_data.get("pending", 0),
            "approved_cases": validation_data.get("approved", 0),
            "rejected_cases": validation_data.get("rejected", 0),
            "series": daily_records.to_dict(orient="records"),
            "goal": int(os.getenv("RPPS_RECENSUS_GOAL", "0")) or None,
        }

        if format == "json":
            return report

        if format == "csv":
            csv = daily_records.to_csv(index=False, encoding="utf-8")
            return Response(
                content=csv,
                media_type="text/csv; charset=utf-8",
                headers={"Content-Disposition": 'attachment; filename="rpps_executive_report.csv"'},
            )

        fig, ax = plt.subplots(figsize=(8, 4))
        if not daily_records.empty:
            ax.bar(daily_records["day"].dt.strftime("%d/%m"), daily_records["count"], color="#2a6df4")
        ax.set_title("Registros RPPS por dia")
        ax.set_xlabel("Dia")
        ax.set_ylabel("Quantidade")
        ax.grid(axis="y", linestyle="--", alpha=0.3)
        fig.tight_layout()
        buffer = io.BytesIO()
        fig.savefig(buffer, format="png", dpi=150)
        plt.close(fig)
        return Response(
            content=buffer.getvalue(),
            media_type="image/png",
            headers={"Content-Disposition": 'attachment; filename="rpps_executive_report.png"'},
        )

    return app


app = create_app()
