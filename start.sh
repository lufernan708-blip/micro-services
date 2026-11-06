#!/usr/bin/env bash
set -Eeuo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")"

for command in docker curl openssl; do
    if ! command -v "$command" >/dev/null 2>&1; then
        printf 'Required command not found: %s\n' "$command" >&2
        exit 1
    fi
done

if [[ ! -f .env ]]; then
    umask 077
    jwt_secret="$(openssl rand -hex 32)"
    login_password="$(openssl rand -hex 16)"
    rabbitmq_password="$(openssl rand -hex 16)"
    cat > .env <<EOF
JWT_SECRET_KEY=${jwt_secret}
LOGIN_USERNAME=admin
LOGIN_PASSWORD=${login_password}
RABBITMQ_DEFAULT_USER=microservices
RABBITMQ_DEFAULT_PASS=${rabbitmq_password}
EOF
    printf 'Created local development configuration in .env (ignored by Git).\n'
fi
chmod 600 .env

docker compose up --build --detach --wait --wait-timeout 180

printf '\nService status:\n'
docker compose ps

printf '\nChecking gateway and internal service endpoints...\n'
for url in \
    http://127.0.0.1:8000/openapi.json \
    http://127.0.0.1:8001/openapi.json \
    http://127.0.0.1:8002/openapi.json \
    http://127.0.0.1:8080/; do
    if ! curl --fail --silent --show-error --output /dev/null "$url"; then
        printf 'Health check failed: %s\n' "$url" >&2
        docker compose logs --tail=80
        exit 1
    fi
    printf 'OK  %s\n' "$url"
done

printf '\nGateway: http://127.0.0.1:8000\nFrontend: http://127.0.0.1:8080\n'
printf 'Swagger UI: http://127.0.0.1:8000/docs\n'
printf 'User and order services listen on ports 8001 and 8002 (local diagnostics only).\n'
printf 'Demo login values are stored in the local, Git-ignored .env file.\n'
