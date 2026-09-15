#!/usr/bin/env bash
set -euo pipefail

cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."

# Запускаем группы по очереди, чтобы уменьшить пиковую нагрузку при инициализации.
for service in configSrv-1 shard1-1 shard2-1; do
  prefix="${service%-1}"
  docker compose up -d --quiet-pull --wait --wait-timeout 120 \
    "${prefix}-1" "${prefix}-2" "${prefix}-3"
  port=27018
  if [[ "$service" == configSrv-1 ]]; then
    port=27019
  fi
  docker compose exec -T "$service" mongosh --port "$port" --quiet \
    --file /scripts/init-replica-set.js
done

docker compose up -d --wait --wait-timeout 180
docker compose exec -T mongos mongosh --port 27017 --quiet \
  --file /scripts/init-cluster.js
# Initial data loading bypasses the API's POST invalidation.
docker compose exec -T redis redis-cli DEL api:cache:somedb:users:v1:helloDoc
docker compose exec -T mongos mongosh --port 27017 --quiet \
  --file /scripts/check-sharding.js

for service in configSrv-1 shard1-1 shard2-1; do
  port=27018
  if [[ "$service" == configSrv-1 ]]; then
    port=27019
  fi
  docker compose exec -T "$service" mongosh --port "$port" --quiet \
    --file /scripts/check-replication.js
done

printf '\nПриложение: http://localhost:%s\n' "${APP_PORT:-8080}"
