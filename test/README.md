# Cenários de teste

Os testes usam bancos SQLite temporários e chamadas ASGI locais; não enviam requisições a serviços de terceiros.

## Executar

Na raiz do projeto, configure o `.env` conforme `.env.example` e execute:

```bash
docker compose --profile test run --build --rm test-suite
```

Com as dependências Python instaladas localmente, `python test/test.py` executa a mesma suíte padrão.

O comando roda os testes unitários, regressivos, exploratórios e de interceptação. O cenário de carga fica separado para não ser executado por engano:

```bash
TEST_LOAD_REQUESTS=1000 docker compose --profile test run --build --rm test-suite pytest -q -m load -s
```

O cenário de carga executa somente contra o User Service ASGI em processo, com concorrência máxima de 25 e limite de 2.000 requisições por execução. Não aponte essa configuração a serviços de produção.

## Cobertura dos cenários

- `test_unit.py`: criação, consulta, duplicidade e usuário inexistente.
- `test_regression.py`: emissão e validação JWT, rotas protegidas e gravação atômica do pedido/outbox.
- `test_exploratory.py`: valores nos limites e entradas inválidas do schema de pedidos.
- `test_interception.py`: captura simulada do request de saída do gateway para verificar body, query e filtragem/substituição de headers; nenhum tráfego real é interceptado.
- `test_load.py`: criação concorrente e limitada de usuários, com throughput e latência média/p95 no relatório.
