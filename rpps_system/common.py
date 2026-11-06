from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

MAX_UPLOAD_SIZE_BYTES = 10 * 1024 * 1024
ALLOWED_DOCUMENT_MIME_TYPES = {
    "application/pdf",
    "image/png",
    "image/jpeg",
}
ALLOWED_DOCUMENT_SUFFIXES = {".pdf", ".png", ".jpg", ".jpeg"}


def database_path(environment_variable: str, default_name: str) -> str:
    path = os.getenv(environment_variable, f"./data/{default_name}")
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    return path


def sanitize_text(value: str | None, *, max_length: int = 255) -> str | None:
    if value is None:
        return None
    cleaned = " ".join(str(value).strip().split())
    if not cleaned:
        return None
    return cleaned[:max_length]


def normalize_status(value: str | None) -> str:
    if value is None:
        return "pending"
    normalized = value.strip().lower().replace("_", " ")
    mapping = {
        "pending": "pending",
        "pendente": "pending",
        "approved": "approved",
        "aprovado": "approved",
        "approve": "approved",
        "rejected": "rejected",
        "rejeitado": "rejected",
        "reject": "rejected",
        "nao aprovado": "rejected",
        "não aprovado": "rejected",
        "nao_aprovado": "rejected",
        "não_aprovado": "rejected",
    }
    return mapping.get(normalized, "pending")


def validate_document_payload(filename: str, content_type: str | None, content: bytes) -> dict[str, str | int]:
    if len(content) == 0:
        raise ValueError("Arquivo vazio.")
    if len(content) > MAX_UPLOAD_SIZE_BYTES:
        raise ValueError("Arquivo excede 10 MB.")

    suffix = Path(filename or "").suffix.lower()
    if suffix not in ALLOWED_DOCUMENT_SUFFIXES:
        raise ValueError("Formato de arquivo inválido. Use PDF, JPG ou PNG.")

    mime = (content_type or "").split(";", 1)[0].strip().lower()
    if mime not in ALLOWED_DOCUMENT_MIME_TYPES:
        raise ValueError("Tipo MIME do arquivo não é permitido.")

    if mime == "application/pdf" and not content.startswith(b"%PDF"):
        raise ValueError("Documento PDF inválido ou corrompido.")
    if mime == "image/png" and not content.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ValueError("Arquivo PNG inválido ou corrompido.")
    if mime == "image/jpeg" and not (content.startswith(b"\xff\xd8\xff") or content.startswith(b"\x89PNG")):
        raise ValueError("Arquivo JPEG inválido ou corrompido.")

    import hashlib

    return {
        "filename": Path(filename).name,
        "content_type": mime,
        "size_bytes": len(content),
        "sha256": hashlib.sha256(content).hexdigest(),
    }


@contextmanager
def connection(path: str) -> Iterator[sqlite3.Connection]:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=10)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    db.execute("PRAGMA busy_timeout = 10000")
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
