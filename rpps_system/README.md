# Plataforma de Recenseamento RPPS

Aplicação de demonstração com serviços FastAPI independentes, gateway JWT e frontend React/Tailwind.

## Iniciar no Codespaces

Na raiz do repositório:

```bash
chmod +x rpps_system/start-rpps.sh
./rpps_system/start-rpps.sh
```

O script cria `.env.rpps` com um usuário administrativo local e segredos aleatórios, sem ler nem sobrescrever `.env`. A senha inicial fica no arquivo local com permissões restritas. Para entrar, use o usuário `rpps.admin` e consulte `RPPS_ADMIN_PASSWORD` em `.env.rpps`. Não reutilize essa conta em produção.

Abra a porta **8080** para o painel e a porta **8000** em `/docs` para explorar o gateway. Somente o gateway e o frontend são publicados; Auth, Core, Validation e Analytics aceitam conexões apenas na rede Docker.

## Serviços e fluxo

```text
Browser → React/Tailwind (:8080) → Nginx /api → API Gateway (:8000)
                                              ├─ Auth Service (:8001) → auth.db
                                              ├─ RPPS Core (:8002) → core.db
                                              ├─ Validation (:8003) → validation.db
                                              └─ Analytics (:8004) → analytics.db
```

- **Auth:** cria a conta administrativa de desenvolvimento na base isolada, armazena hash scrypt da senha e emite JWT com validade de 30 minutos.
- **Gateway:** valida assinatura, issuer, audience e expiração do token; encaminha uma identidade interna derivada do JWT. Os serviços internos não têm portas publicadas.
- **RPPS Core:** registra dados pessoais, matrícula, cargo, lotação, contribuição, dependentes e históricos funcionais/previdenciários. CPF é único.
- **Validation:** abre uma ficha por segurado, sinaliza dados obrigatórios ausentes ou CPF inválido e permite aprovar/rejeitar fichas pendentes.
- **Analytics:** consulta Core e Validation em paralelo, persiste snapshots e entrega totais reais e registros diários. O dashboard atualiza periodicamente; meta só é calculada quando `RPPS_RECENSUS_GOAL` for configurada.

O envio do formulário é assíncrono via HTTP. O gateway registra o segurado no Core e abre a ficha de validação; se a segunda chamada falhar, retorna erro explícito e o segurado permanece persistido para reconciliação, sem criar um sucesso fictício.

## Testes

```bash
python -m pip install -r rpps_system/requirements.txt
PYTHONPATH=rpps_system python -m pytest -q -c /dev/null -p no:cacheprovider rpps_system/tests
```

The service tests cover login/token issuance, duplicate cadastro, validation decisions, gateway authentication/context forwarding, and analytics snapshot retention.

## Segurança e produção

Os bancos são separados em volumes Docker, mas SQLite e a conta bootstrap destinam-se somente à demonstração local. Os dados RPPS contêm informações pessoais; não cadastre dados reais neste ambiente. Para produção, use PostgreSQL gerenciado, TLS, gestão de segredos, controle de acesso por papel, trilha de auditoria, criptografia em repouso, política de retenção/consentimento, cópias de segurança e uma reconciliação durável entre Core e Validation. A amostra de CPF é apenas uma validação de dígitos e não consulta situação cadastral oficial.

Para parar a stack sem remover volumes:

```bash
docker compose --env-file .env.rpps -f rpps_system/docker-compose.yml -p rpps down
```
