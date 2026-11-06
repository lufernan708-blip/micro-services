from __future__ import annotations

import json
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, Literal
from uuid import uuid4

from fastapi import FastAPI, Header, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field

from common import connection, database_path, normalize_status, sanitize_text


class ValidationCase(BaseModel):
    id: str
    insured_id: str
    insured_name: str
    status: Literal["pending", "approved", "rejected"]
    issues: list[str]
    created_at: datetime
    reviewed_at: datetime | None = None
    reviewed_by: str | None = None
    status_label: str | None = None


class Decision(BaseModel):
    decision: Literal["approve", "reject"]
    note: str | None = Field(default=None, max_length=500)


REQUIRED_DOCS_BY_CATEGORY = {
    "Servidor": ["identidade", "comprovante_vinculo"],
    "Aposentado": ["identidade", "comprovante_aposentadoria"],
    "Pensionista": ["identidade", "comprovante_pensao"],
}


def valid_cpf(value: str) -> bool:
    digits = "".join(character for character in value if character.isdigit())
    if len(digits) != 11 or len(set(digits)) == 1:
        return False
    for length in (9, 10):
        checksum = sum(int(digits[index]) * (length + 1 - index) for index in range(length))
        check_digit = (checksum * 10) % 11
        if check_digit == 10:
            check_digit = 0
        if check_digit != int(digits[length]):
            return False
    return True


def normalize_category(value: Any) -> str:
    if value is None:
        return "Servidor"
    normalized = str(value).strip().lower()
    mapping = {
        "servidor": "Servidor",
        "aposentado": "Aposentado",
        "pensionista": "Pensionista",
    }
    return mapping.get(normalized, "Servidor")


def find_issues(payload: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    cpf_value = str(payload.get("cpf", "") or "")
    if not valid_cpf(cpf_value):
        issues.append("CPF ausente ou com dígitos verificadores inválidos.")
    if not str(payload.get("registration_number") or "").strip():
        issues.append("Matrícula funcional não informada.")
    if not str(payload.get("position") or "").strip():
        issues.append("Cargo não informado.")
    if not str(payload.get("department") or "").strip():
        issues.append("Lotação não informada.")
    if not payload.get("admission_date"):
        issues.append("Data de admissão não informada.")
    if not payload.get("date_of_birth"):
        issues.append("Data de nascimento não informada.")
    if not payload.get("email") and not payload.get("phone"):
        issues.append("Nenhum canal de contato foi informado.")

    category = normalize_category(payload.get("category"))
    required_documents = REQUIRED_DOCS_BY_CATEGORY.get(category, [])
    uploaded_documents = payload.get("documents") or []
    present = {str(doc.get("kind", "")).strip().lower() for doc in uploaded_documents if isinstance(doc, dict)}
    for required in required_documents:
        if required not in present:
            issues.append(
                f"Documento obrigatório ausente para {category}: {required}."
            )
    return issues


def create_app(db_path: str | None = None) -> FastAPI:
    path = db_path or database_path("RPPS_VALIDATION_DB_PATH", "validation.db")

    def initialize() -> None:
        with connection(path) as db:
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS validation_cases (
                    id TEXT PRIMARY KEY,
                    insured_id TEXT NOT NULL UNIQUE,
                    insured_name TEXT NOT NULL,
                    status TEXT NOT NULL,
                    issues TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    reviewed_at TEXT,
                    reviewed_by TEXT,
                    review_note TEXT
                )
                """
            )
            db.execute(
                "CREATE INDEX IF NOT EXISTS idx_validation_status ON validation_cases(status, created_at)"
            )

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        initialize()
        yield

    app = FastAPI(title="RPPS Validation Service", version="1.0.0", lifespan=lifespan)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"service": "validation", "status": "ok"}

    @app.post("/cases", response_model=ValidationCase, status_code=status.HTTP_201_CREATED)
    async def evaluate(
        payload: dict[str, Any],
        _: str = Header(alias="X-Actor-Id"),
    ) -> ValidationCase:
        insured_id = str(payload.get("id", ""))
        insured_name = str(payload.get("full_name", "")).strip()
        if not insured_id or not insured_name:
            raise HTTPException(status_code=422, detail="Insured identifier and name are required.")

        issues = find_issues(payload)
        case_id = str(uuid4())
        now = datetime.now(timezone.utc)
        case_status = "pending" if issues else "approved"
        with connection(path) as db:
            try:
                db.execute(
                    """
                    INSERT INTO validation_cases
                        (id, insured_id, insured_name, status, issues, created_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        case_id,
                        insured_id,
                        insured_name,
                        case_status,
                        json.dumps(issues, ensure_ascii=False),
                        now.isoformat(),
                    ),
                )
            except Exception as exc:
                if "UNIQUE constraint failed" in str(exc):
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail="A validation case already exists for this record.",
                    ) from exc
                raise
        return ValidationCase(
            id=case_id,
            insured_id=insured_id,
            insured_name=insured_name,
            status=case_status,
            issues=issues,
            created_at=now,
            status_label={"pending": "Pendente", "approved": "Aprovado", "rejected": "Não Aprovado"}.get(case_status, case_status),
        )

    def decode_case(row: Any) -> ValidationCase:
        item = ValidationCase(
            id=row["id"],
            insured_id=row["insured_id"],
            insured_name=row["insured_name"],
            status=row["status"],
            issues=json.loads(row["issues"]),
            created_at=row["created_at"],
            reviewed_at=row["reviewed_at"],
            reviewed_by=row["reviewed_by"],
            status_label={"pending": "Pendente", "approved": "Aprovado", "rejected": "Não Aprovado"}.get(row["status"], row["status"]),
        )
        return item

    @app.get("/cases")
    async def list_cases(
        case_status: str = Query(default="pending", alias="status", pattern="^(pending|approved|rejected|all)$"),
        limit: int = Query(default=100, ge=1, le=500),
    ) -> dict[str, Any]:
        with connection(path) as db:
            if case_status == "all":
                rows = db.execute(
                    "SELECT * FROM validation_cases ORDER BY created_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()
            else:
                rows = db.execute(
                    """
                    SELECT * FROM validation_cases WHERE status = ?
                    ORDER BY created_at DESC LIMIT ?
                    """,
                    (case_status, limit),
                ).fetchall()
            total = db.execute(
                "SELECT COUNT(*) AS count FROM validation_cases"
                if case_status == "all"
                else "SELECT COUNT(*) AS count FROM validation_cases WHERE status = ?",
                () if case_status == "all" else (case_status,),
            ).fetchone()["count"]
        return {"items": [decode_case(row).model_dump(mode="json") for row in rows], "total": total}

    @app.get("/cases/stats")
    async def case_stats() -> dict[str, int]:
        with connection(path) as db:
            rows = db.execute(
                "SELECT status, COUNT(*) AS count FROM validation_cases GROUP BY status"
            ).fetchall()
        counts = {row["status"]: row["count"] for row in rows}
        return {
            "pending": counts.get("pending", 0),
            "approved": counts.get("approved", 0),
            "rejected": counts.get("rejected", 0),
            "total": sum(counts.values()),
        }

    @app.patch("/cases/{case_id}", response_model=ValidationCase)
    async def decide_case(
        case_id: str,
        decision: Decision,
        actor: str = Header(alias="X-Actor-Id"),
    ) -> ValidationCase:
        now = datetime.now(timezone.utc)
        new_status = "approved" if decision.decision == "approve" else "rejected"
        with connection(path) as db:
            result = db.execute(
                """
                UPDATE validation_cases
                SET status = ?, reviewed_at = ?, reviewed_by = ?, review_note = ?
                WHERE id = ? AND status = 'pending'
                """,
                (new_status, now.isoformat(), actor, decision.note, case_id),
            )
            if result.rowcount == 0:
                exists = db.execute(
                    "SELECT 1 FROM validation_cases WHERE id = ?",
                    (case_id,),
                ).fetchone()
                if exists is None:
                    raise HTTPException(status_code=404, detail="Validation case not found.")
                raise HTTPException(status_code=409, detail="Validation case has already been reviewed.")
            row = db.execute(
                "SELECT * FROM validation_cases WHERE id = ?",
                (case_id,),
            ).fetchone()
        return decode_case(row)

    return app


app = create_app()
