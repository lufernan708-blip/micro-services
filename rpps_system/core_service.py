from __future__ import annotations

import json
import sqlite3
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, File, Form, Header, HTTPException, Query, UploadFile, status
from pydantic import BaseModel, ConfigDict, Field, field_validator

from common import (
    MAX_UPLOAD_SIZE_BYTES,
    connection,
    database_path,
    normalize_status,
    sanitize_text,
    validate_document_payload,
)


class Dependent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=160)
    cpf: str | None = Field(default=None, max_length=14)
    relationship: str | None = Field(default=None, max_length=50)
    date_of_birth: date | None = None


class EmploymentPeriod(BaseModel):
    model_config = ConfigDict(extra="forbid")

    employer: str = Field(min_length=1, max_length=160)
    position: str | None = Field(default=None, max_length=120)
    start_date: date
    end_date: date | None = None


class ContributionPeriod(BaseModel):
    model_config = ConfigDict(extra="forbid")

    institution: str = Field(min_length=1, max_length=160)
    regime: str = Field(min_length=1, max_length=80)
    start_date: date
    end_date: date | None = None
    months: int | None = Field(default=None, ge=0, le=840)


class DocumentRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: str = Field(min_length=2, max_length=80)
    filename: str = Field(min_length=1, max_length=180)
    content_type: str = Field(min_length=3, max_length=80)
    size_bytes: int = Field(default=0, ge=0, le=MAX_UPLOAD_SIZE_BYTES)
    sha256: str = Field(min_length=64, max_length=64)
    uploaded_at: datetime | None = None
    uploaded_by: str | None = None

    @field_validator("kind")
    @classmethod
    def normalize_kind(cls, value: str) -> str:
        return sanitize_text(value, max_length=80) or value


class InsuredCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    full_name: str = Field(min_length=1, max_length=160)
    cpf: str = Field(min_length=1, max_length=14)
    date_of_birth: date | None = None
    email: str | None = Field(default=None, max_length=254)
    phone: str | None = Field(default=None, max_length=32)
    address: str | None = Field(default=None, max_length=240)
    municipality: str | None = Field(default=None, max_length=120)
    registration_number: str | None = Field(default=None, max_length=40)
    position: str | None = Field(default=None, max_length=120)
    department: str | None = Field(default=None, max_length=120)
    admission_date: date | None = None
    employment_status: str = Field(default="active", max_length=30)
    contribution_years: int | None = Field(default=None, ge=0, le=70)
    contribution_months: int | None = Field(default=None, ge=0, le=11)
    category: str = Field(default="Servidor", min_length=2, max_length=80)
    status: str = Field(default="pending", min_length=2, max_length=30)
    dependents: list[Dependent] = Field(default_factory=list, max_length=30)
    employment_history: list[EmploymentPeriod] = Field(default_factory=list, max_length=50)
    contribution_history: list[ContributionPeriod] = Field(default_factory=list, max_length=50)
    documents: list[DocumentRecord] = Field(default_factory=list, max_length=20)

    @field_validator("category")
    @classmethod
    def valid_category(cls, value: str) -> str:
        normalized = (value or "Servidor").strip()
        allowed = {"servidor", "aposentado", "pensionista"}
        lowered = normalized.lower()
        if lowered not in allowed:
            raise ValueError("Category must be one of: Servidor, Aposentado, Pensionista.")
        return {
            "servidor": "Servidor",
            "aposentado": "Aposentado",
            "pensionista": "Pensionista",
        }[lowered]

    @field_validator("status")
    @classmethod
    def valid_status(cls, value: str) -> str:
        return normalize_status(value)


class InsuredRecord(InsuredCreate):
    id: str
    created_at: datetime
    created_by: str


REQUIRED_DOCUMENTS = {
    "Servidor": ["identidade", "comprovante_vinculo"],
    "Aposentado": ["identidade", "comprovante_aposentadoria"],
    "Pensionista": ["identidade", "comprovante_pensao"],
}


def create_app(db_path: str | None = None) -> FastAPI:
    path = db_path or database_path("RPPS_CORE_DB_PATH", "core.db")

    def initialize() -> None:
        with connection(path) as db:
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS insured (
                    id TEXT PRIMARY KEY,
                    cpf TEXT NOT NULL,
                    full_name TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    created_by TEXT NOT NULL
                )
                """
            )
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS insured_documents (
                    id TEXT PRIMARY KEY,
                    insured_id TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    filename TEXT NOT NULL,
                    content_type TEXT NOT NULL,
                    size_bytes INTEGER NOT NULL,
                    sha256 TEXT NOT NULL,
                    uploaded_at TEXT NOT NULL,
                    uploaded_by TEXT NOT NULL,
                    FOREIGN KEY (insured_id) REFERENCES insured(id) ON DELETE CASCADE
                )
                """
            )
            db.execute("CREATE INDEX IF NOT EXISTS idx_insured_created_at ON insured(created_at)")
            db.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_insured_cpf ON insured(cpf)")
            db.execute(
                "CREATE INDEX IF NOT EXISTS idx_insured_category ON insured((json_extract(payload, '$.category')))")

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        initialize()
        yield

    app = FastAPI(title="RPPS Core Service", version="1.0.0", lifespan=lifespan)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"service": "rpps-core", "status": "ok"}

    @app.post("/insured", response_model=InsuredRecord, status_code=status.HTTP_201_CREATED)
    async def register_insured(
        payload: InsuredCreate,
        actor: str = Header(alias="X-Actor-Id"),
    ) -> InsuredRecord:
        insured_id = str(uuid4())
        now = datetime.now(timezone.utc)
        values = payload.model_dump(mode="json")
        record = InsuredRecord(**values, id=insured_id, created_at=now, created_by=actor)

        required = REQUIRED_DOCUMENTS.get(payload.category, [])
        present = {str(doc.kind).lower() for doc in payload.documents}
        missing = [name for name in required if name not in present]
        if missing:
            missing_joined = ", ".join(missing)
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Documentos obrigatórios ausentes para {payload.category}: {missing_joined}.",
            )

        with connection(path) as db:
            try:
                db.execute(
                    """
                    INSERT INTO insured (id, cpf, full_name, payload, created_at, created_by)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        insured_id,
                        payload.cpf,
                        payload.full_name,
                        json.dumps(values, ensure_ascii=False),
                        now.isoformat(),
                        actor,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                if "UNIQUE constraint failed" in str(exc):
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail="An insured person with this CPF already exists.",
                    ) from exc
                raise

        for document in payload.documents:
            document_id = str(uuid4())
            with connection(path) as db:
                db.execute(
                    """
                    INSERT INTO insured_documents (
                        id, insured_id, kind, filename, content_type, size_bytes, sha256, uploaded_at, uploaded_by
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        document_id,
                        insured_id,
                        document.kind,
                        document.filename,
                        document.content_type,
                        document.size_bytes,
                        document.sha256,
                        (document.uploaded_at or now).isoformat(),
                        document.uploaded_by or actor,
                    ),
                )

        return record

    def decode_record(row: Any) -> InsuredRecord:
        payload = json.loads(row["payload"])
        payload.setdefault("documents", [])
        return InsuredRecord(
            **payload,
            id=row["id"],
            created_at=row["created_at"],
            created_by=row["created_by"],
        )

    @app.get("/insured/stats")
    async def insured_stats() -> dict[str, Any]:
        with connection(path) as db:
            total = db.execute("SELECT COUNT(*) AS count FROM insured").fetchone()["count"]
            today = date.today().isoformat()
            today_count = db.execute(
                "SELECT COUNT(*) AS count FROM insured WHERE substr(created_at, 1, 10) = ?",
                (today,),
            ).fetchone()["count"]
            rows = db.execute(
                """
                SELECT substr(created_at, 1, 10) AS day, COUNT(*) AS count
                FROM insured
                WHERE created_at >= ?
                GROUP BY substr(created_at, 1, 10)
                ORDER BY day
                """,
                ((datetime.now(timezone.utc) - timedelta(days=6)).isoformat(),),
            ).fetchall()
            category_rows = db.execute(
                "SELECT json_extract(payload, '$.category') AS category, COUNT(*) AS count FROM insured GROUP BY json_extract(payload, '$.category')"
            ).fetchall()
        return {
            "total": total,
            "registered_today": today_count,
            "registrations_by_day": [dict(row) for row in rows],
            "categories": {row["category"] or "Servidor": row["count"] for row in category_rows},
        }

    @app.get("/insured")
    async def list_insured(
        search: str | None = Query(default=None, max_length=160),
        limit: int = Query(default=100, ge=1, le=500),
        offset: int = Query(default=0, ge=0),
    ) -> dict[str, Any]:
        with connection(path) as db:
            if search:
                pattern = f"%{search.strip()}%"
                total = db.execute(
                    "SELECT COUNT(*) AS count FROM insured WHERE full_name LIKE ? OR cpf LIKE ?",
                    (pattern, pattern),
                ).fetchone()["count"]
                rows = db.execute(
                    """
                    SELECT id, cpf, full_name, payload, created_at, created_by
                    FROM insured WHERE full_name LIKE ? OR cpf LIKE ?
                    ORDER BY created_at DESC LIMIT ? OFFSET ?
                    """,
                    (pattern, pattern, limit, offset),
                ).fetchall()
            else:
                total = db.execute("SELECT COUNT(*) AS count FROM insured").fetchone()["count"]
                rows = db.execute(
                    """
                    SELECT id, cpf, full_name, payload, created_at, created_by
                    FROM insured ORDER BY created_at DESC LIMIT ? OFFSET ?
                    """,
                    (limit, offset),
                ).fetchall()
        return {"items": [decode_record(row).model_dump(mode="json") for row in rows], "total": total}

    @app.post("/insured/{insured_id}/documents", status_code=status.HTTP_201_CREATED)
    async def upload_document(
        insured_id: str,
        file: UploadFile = File(...),
        kind: str = Form(...),
        actor: str = Header(alias="X-Actor-Id"),
    ) -> dict[str, Any]:
        with connection(path) as db:
            insured = db.execute("SELECT id FROM insured WHERE id = ?", (insured_id,)).fetchone()
        if insured is None:
            raise HTTPException(status_code=404, detail="Insured record not found.")

        file_bytes = await file.read()
        try:
            details = validate_document_payload(file.filename or "upload.bin", file.content_type, file_bytes)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

        document_kind = sanitize_text(kind, max_length=80) or "documento"
        document = DocumentRecord(
            kind=document_kind,
            filename=str(details["filename"]),
            content_type=str(details["content_type"]),
            size_bytes=int(details["size_bytes"]),
            sha256=str(details["sha256"]),
            uploaded_at=datetime.now(timezone.utc),
            uploaded_by=actor,
        )
        document_id = str(uuid4())
        with connection(path) as db:
            db.execute(
                """
                INSERT INTO insured_documents (
                    id, insured_id, kind, filename, content_type, size_bytes, sha256, uploaded_at, uploaded_by
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    document_id,
                    insured_id,
                    document.kind,
                    document.filename,
                    document.content_type,
                    document.size_bytes,
                    document.sha256,
                    document.uploaded_at.isoformat(),
                    document.uploaded_by,
                ),
            )
        return {"insured_id": insured_id, "document": document.model_dump(mode="json")}

    @app.get("/insured/{insured_id}/documents")
    async def list_documents(insured_id: str) -> dict[str, Any]:
        with connection(path) as db:
            row = db.execute("SELECT id FROM insured WHERE id = ?", (insured_id,)).fetchone()
            if row is None:
                raise HTTPException(status_code=404, detail="Insured record not found.")
            rows = db.execute(
                """
                SELECT kind, filename, content_type, size_bytes, sha256, uploaded_at, uploaded_by
                FROM insured_documents WHERE insured_id = ? ORDER BY uploaded_at DESC
                """,
                (insured_id,),
            ).fetchall()
        return {"items": [dict(item) for item in rows], "total": len(rows)}

    @app.get("/insured/{insured_id}", response_model=InsuredRecord)
    async def get_insured(insured_id: str) -> InsuredRecord:
        with connection(path) as db:
            row = db.execute(
                "SELECT id, cpf, full_name, payload, created_at, created_by FROM insured WHERE id = ?",
                (insured_id,),
            ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Insured record not found.")
        return decode_record(row)

    return app


app = create_app()
