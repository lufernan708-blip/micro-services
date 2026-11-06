# RPPS+ — Plataforma de Recenseamento Previdenciário

Este repositório contém uma demonstração de recenseamento RPPS composta por frontend React/Tailwind, API Gateway com JWT e microsserviços independentes de autenticação, cadastro, validação e analytics.

## Executar

Com Docker Compose disponível, na raiz:

```bash
./rpps_system/start-rpps.sh
```

O script cria `.env.rpps` localmente com credenciais aleatórias de desenvolvimento (arquivo ignorado pelo Git), inicia os serviços e verifica as URLs publicadas:

- Frontend: `http://localhost:8080`
- Gateway e documentação: `http://localhost:8000/docs`

Use o usuário `rpps.admin`; a senha local de demonstração é gerada em `.env.rpps`. Não use esses dados em produção. Para parar sem remover volumes:

```bash
docker compose --env-file .env.rpps -f rpps_system/docker-compose.yml -p rpps down
```

## Arquitetura e segurança

Auth, Core, Validation e Analytics têm bases SQLite próprias e não publicam portas no host. O Gateway é o ponto único de entrada; só ele valida JWT e repassa a identidade autenticada às chamadas internas. Dados pessoais no exemplo são fictícios e não devem ser usados em produção. SQLite, credenciais bootstrap e ausência de controles operacionais avançados tornam esta uma implementação didática, não uma instalação produtiva.

Veja [rpps_system/README.md](./rpps_system/README.md) para detalhes dos serviços, fluxo, testes e recomendações de produção.
