#!/usr/bin/env bash
set -euo pipefail

cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."

# Сначала запускаем mongod: до rs.initiate mongos ещё не готов к работе.
docker compose up -d --quiet-pull --wait --wait-timeout 120 configSrv-1 shard1-1 shard2-1

for service in configSrv-1 shard1-1 shard2-1; do
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
docker compose exec -T mongos mongosh --port 27017 --quiet \
  --file /scripts/check-sharding.js

printf '\nПриложение: http://localhost:%s\n' "${APP_PORT:-8080}"
