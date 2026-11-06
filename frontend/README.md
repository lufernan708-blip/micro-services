# Microstore frontend

Frontend React + TypeScript with Vite for the FastAPI microservices.

## Subir o projeto com Docker Compose

Na raiz do repositório, prepare as credenciais:

```bash
cp .env.example .env
```

Edite `.env`: gere `JWT_SECRET_KEY` com `openssl rand -hex 32`, escolha credenciais fortes para `LOGIN_USERNAME` e `LOGIN_PASSWORD`, e configure uma senha RabbitMQ alfanumérica para `RABBITMQ_DEFAULT_PASS`. Não reutilize os valores de exemplo em produção.

Suba a stack:

```bash
docker compose up --build
```

Abra `http://localhost:8080` e entre com o usuário e a senha definidos em `.env`. A API Gateway fica em `http://localhost:8000`; os serviços de usuários e pedidos ficam acessíveis apenas na rede interna do Compose. O painel RabbitMQ está em `http://localhost:15672`, acessível somente pela máquina local. SQLite e RabbitMQ usam volumes persistentes.

Para encerrar os containers, use `Ctrl+C` e depois `docker compose down`. Os volumes permanecem para a próxima execução. Para removê-los também, execute `docker compose down --volumes`.

### JWT e proteção das rotas

`POST /login` recebe `{"username":"...","password":"..."}` e retorna um bearer token HS256 válido por 30 minutos. As rotas `/orders` exigem `Authorization: Bearer <token>`; o gateway valida assinatura, emissor e expiração, remove headers de identidade enviados pelo cliente e encaminha `X-Authenticated-User` para o serviço de pedidos. As credenciais de login são configuradas por ambiente e não são armazenadas no banco de usuários. Em produção, injete os segredos por um gerenciador de secrets e use TLS.

### Eventos e RabbitMQ

Após validar o usuário, o serviço grava o pedido e o evento `order.created` na mesma transação SQLite (outbox). Um publicador em segundo plano envia eventos persistentes para a exchange topic `orders`; o worker `notification-worker` consome a fila durável `order-notifications` e registra o processamento no log. O campo `event_id` identifica cada evento. A entrega é *at-least-once*: consumidores com efeitos externos devem ser idempotentes.

Para testar o broker sem Compose, inicie-o com:

```bash
docker run -d --name rabbitmq \
  -p 127.0.0.1:5672:5672 -p 127.0.0.1:15672:15672 \
  -e RABBITMQ_DEFAULT_USER=app \
  -e RABBITMQ_DEFAULT_PASS=local_dev_password \
  rabbitmq:4-management
```

Configure `RABBITMQ_URL=amqp://app:local_dev_password@127.0.0.1:5672/` para o Order Service e o worker ao executá-los localmente. Para parar o broker de teste, use `docker stop rabbitmq` e `docker rm rabbitmq`.

## Executar localmente

Depois de preparar o `.env`, inicie os serviços e o worker em containers:

```bash
docker compose up --build rabbitmq user-service order-service api-gateway notification-worker
```

Em outro terminal, instale as dependências do frontend uma vez e inicie o Vite:

```bash
npm install
npm run dev
```

Abra o endereço informado pelo Vite. Durante o desenvolvimento, as chamadas de `/api` são encaminhadas para `http://127.0.0.1:8000`.

## Interface do painel

O dashboard chama somente operações que existem na API:

- `GET /openapi.json` pelo gateway para verificar conectividade.
- `POST /login` para iniciar sessão JWT.
- `POST /users` para cadastrar e selecionar um usuário.
- `POST /orders` para criar pedido autenticado; o Order Service valida o usuário.
- `GET /users/{user_id}` e `GET /orders/user/{user_id}` para consultar o cliente e seus pedidos.

Os cartões resumem apenas os usuários, pedidos e valores criados na sessão atual do navegador; não representam totais persistidos. A tabela mostra pedidos realmente retornados pela API ou criados na sessão. Este backend não fornece leituras de laminação, bobinas ou qualidade de coroa, portanto a interface não simula esses indicadores.
