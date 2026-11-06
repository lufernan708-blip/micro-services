import os
import sqlite3

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

app = FastAPI(title="User Service", version="1.0.0")
DATABASE_PATH = os.getenv("USERS_DATABASE_PATH", "users.db")

# Inicialização do banco SQLite local
def init_db():
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            email TEXT UNIQUE NOT NULL
        )
    """)
    conn.commit()
    conn.close()

init_db()

# Schemas (Pydantic)
class UserCreate(BaseModel):
    name: str
    email: str

class UserResponse(BaseModel):
    id: int
    name: str
    email: str

@app.post("/users", response_model=UserResponse, status_code=201)
def create_user(user: UserCreate):
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
    try:
        cursor.execute("INSERT INTO users (name, email) VALUES (?, ?)", (user.name, user.email))
        conn.commit()
        user_id = cursor.lastrowid
        return {"id": user_id, "name": user.name, "email": user.email}
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=400, detail="E-mail já cadastrado.")
    finally:
        conn.close()

@app.get("/users/{user_id}", response_model=UserResponse)
def get_user(user_id: int):
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT id, name, email FROM users WHERE id = ?", (user_id,))
    row = cursor.fetchone()
    conn.close()
    
    if not row:
        raise HTTPException(status_code=404, detail="Usuário não encontrado.")
    
    return {"id": row[0], "name": row[1], "email": row[2]}