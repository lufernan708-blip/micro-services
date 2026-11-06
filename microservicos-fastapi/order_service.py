import sqlite3
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import httpx

app = FastAPI(title="Order Service", version="1.0.0")

# URL base do serviço de usuários para comunicação interna
USER_SERVICE_URL = "http://127.0.0.1:8001"

def init_db():
    conn = sqlite3.connect("orders.db")
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            item_name TEXT NOT NULL,
            amount REAL NOT NULL
        )
    """)
    conn.commit()
    conn.close()

init_db()

class OrderCreate(BaseModel):
    user_id: int
    item_name: str
    amount: float

@app.post("/orders", status_code=201)
async def create_order(order: OrderCreate):
    # 1. Comunicação HTTP síncrona: Valida o usuário no User Service
    async with httpx.AsyncClient() as client:
        try:
            response = await client.get(f"{USER_SERVICE_URL}/users/{order.user_id}")
            if response.status_code == 404:
                raise HTTPException(status_code=404, detail="Usuário não existe. Não é possível criar o pedido.")
            elif response.status_code != 200:
                raise HTTPException(status_code=500, detail="Erro ao comunicar com User Service.")
        except httpx.RequestError:
            raise HTTPException(status_code=503, detail="Serviço de Usuários temporariamente indisponível.")

    # 2. Se o usuário for válido, grava o pedido no banco próprio
    conn = sqlite3.connect("orders.db")
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO orders (user_id, item_name, amount) VALUES (?, ?, ?)",
        (order.user_id, order.item_name, order.amount)
    )
    conn.commit()
    order_id = cursor.lastrowid
    conn.close()

    return {"id": order_id, "user_id": order.user_id, "item_name": order.item_name, "amount": order.amount}

@app.get("/orders/user/{user_id}")
def get_orders_by_user(user_id: int):
    conn = sqlite3.connect("orders.db")
    cursor = conn.cursor()
    cursor.execute("SELECT id, user_id, item_name, amount FROM orders WHERE user_id = ?", (user_id,))
    rows = cursor.fetchall()
    conn.close()

    orders = [{"id": r[0], "user_id": r[1], "item_name": r[2], "amount": r[3]} for r in rows]
    return {"user_id": user_id, "orders": orders}