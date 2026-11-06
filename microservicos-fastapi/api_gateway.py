from fastapi import FastAPI, Request, Response
import httpx

app = FastAPI(title="API Gateway", version="1.0.0")

USER_SERVICE_URL = "http://127.0.0.1:8001"
ORDER_SERVICE_URL = "http://127.0.0.1:8002"

# Roteamento genérico para o User Service
@app.api_route("/users/{path:path}", methods=["GET", "POST", "PUT", "DELETE"])
async def proxy_users(request: Request, path: str):
    url = f"{USER_SERVICE_URL}/users/{path}"
    return await forward_request(request, url)

@app.api_route("/users", methods=["POST"])
async def proxy_users_root(request: Request):
    url = f"{USER_SERVICE_URL}/users"
    return await forward_request(request, url)

# Roteamento genérico para o Order Service
@app.api_route("/orders/{path:path}", methods=["GET", "POST", "PUT", "DELETE"])
async def proxy_orders(request: Request, path: str):
    url = f"{ORDER_SERVICE_URL}/orders/{path}"
    return await forward_request(request, url)

@app.api_route("/orders", methods=["POST"])
async def proxy_orders_root(request: Request):
    url = f"{ORDER_SERVICE_URL}/orders"
    return await forward_request(request, url)

# Função auxiliar para retransmitir a requisição
async def forward_request(request: Request, target_url: str):
    body = await request.body()
    async with httpx.AsyncClient() as client:
        try:
            req = client.build_request(
                method=request.method,
                url=target_url,
                headers=dict(request.headers),
                content=body
            )
            res = await client.send(req)
            return Response(content=res.content, status_code=res.status_code, headers=dict(res.headers))
        except httpx.RequestError:
            return Response(content='{"detail": "Microsserviço de destino indisponível."}', status_code=503)
