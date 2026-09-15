#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."

stopped_service=""
restore() {
  if [[ -n "$stopped_service" ]]; then
    docker compose up -d --no-deps --wait --wait-timeout 120 "$stopped_service"
  fi
}
trap restore EXIT

# Проверяем по одному отказу: два шарда и группа config servers.
for prefix in shard1 shard2 configSrv; do
  port=27018
  if [[ "$prefix" == configSrv ]]; then
    port=27019
  fi
  primary=$(docker compose exec -T "${prefix}-1" mongosh --port "$port" --quiet \
    --eval 'print(db.hello().primary)')
  service="${primary%:*}"
  case "$service" in
    "${prefix}-1"|"${prefix}-2"|"${prefix}-3") ;;
    *) printf 'Unexpected primary: %s\n' "$primary" >&2; exit 1 ;;
  esac
  survivor="${prefix}-1"
  if [[ "$service" == "$survivor" ]]; then
    survivor="${prefix}-2"
  fi

  printf '\nStopping primary: %s\n' "$primary"
  stopped_service="$service"
  docker compose stop --timeout 10 "$service"
  docker compose exec -T -e OLD_PRIMARY="$primary" "$survivor" \
    mongosh --port "$port" --quiet --file /scripts/check-election.js
  docker compose exec -T pymongo-api python < scripts/check-api.py

  docker compose up -d --no-deps --wait --wait-timeout 120 "$stopped_service"
  stopped_service=""
  docker compose exec -T "$survivor" mongosh --port "$port" --quiet \
    --file /scripts/check-replication.js
done

printf '\nFailover checks passed for both shards and configRS. All members restored.\n'
