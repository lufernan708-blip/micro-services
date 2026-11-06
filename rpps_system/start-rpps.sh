#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

for command in docker curl openssl; do
  if ! command -v "$command" >/dev/null 2>&1; then
    printf 'Comando obrigatório não encontrado: %s\n' "$command" >&2
    exit 1
  fi
done

if [[ ! -f .env.rpps ]]; then
  umask 077
  cat > .env.rpps <<EOF
RPPS_ADMIN_USERNAME=rpps.admin
RPPS_ADMIN_PASSWORD=$(openssl rand -hex 16)
RPPS_JWT_SECRET=$(openssl rand -hex 32)
RPPS_RECENSUS_GOAL=0
EOF
  printf 'Configuração de desenvolvimento criada em .env.rpps (arquivo ignorado pelo Git).\n'
fi
chmod 600 .env.rpps

legacy_containers="$(docker compose ps -q api-gateway frontend user-service order-service notification-worker rabbitmq)"
if [[ -n "$legacy_containers" ]]; then
  printf 'Parando a stack anterior para liberar as portas 8000 e 8080; dados persistentes serão mantidos.\n'
  docker compose stop api-gateway frontend user-service order-service notification-worker rabbitmq
fi

compose=(docker compose --env-file "$ROOT/.env.rpps" -f "$ROOT/rpps_system/docker-compose.yml" -p rpps)
"${compose[@]}" up --build --detach --wait --wait-timeout 180

for url in \
  http://127.0.0.1:8000/health \
  http://127.0.0.1:8000/openapi.json \
  http://127.0.0.1:8080/; do
  if ! curl --fail --silent --show-error --output /dev/null "$url"; then
    printf 'Verificação indisponível: %s\n' "$url" >&2
    "${compose[@]}" logs --tail=80
    exit 1
  fi
done

printf '\nSistema RPPS ativo:\n'
"${compose[@]}" ps
printf '\nFrontend: http://localhost:8080\nAPI Gateway / Swagger: http://localhost:8000/docs\n'
printf 'Usuário inicial: rpps.admin. A senha temporária está em .env.rpps.\n'
printf 'Parar somente o RPPS: docker compose --env-file .env.rpps -f rpps_system/docker-compose.yml -p rpps down\n'
