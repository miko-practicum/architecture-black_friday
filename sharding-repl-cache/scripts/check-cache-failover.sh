#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."

restore_redis() {
  docker compose up -d --no-deps --wait --wait-timeout 120 redis
}
trap restore_redis EXIT
docker compose stop redis

docker compose exec -T pymongo-api python <<'PY'
import asyncio
import json
import os
import uuid
from urllib.request import Request, urlopen
from motor.motor_asyncio import AsyncIOMotorClient

def request(path, body=None):
    data = None if body is None else json.dumps(body).encode()
    req = Request(f"http://localhost:8080{path}", data=data,
                  headers={"Content-Type": "application/json"})
    with urlopen(req, timeout=15) as response:
        return response.status, json.load(response)

name = f"cache_outage_check_{uuid.uuid4().hex}"
async def cleanup():
    client = AsyncIOMotorClient(os.environ["MONGODB_URL"])
    try:
        await client.somedb.drop_collection(name)
    finally:
        client.close()

try:
    status, users = request("/helloDoc/users")
    assert status == 200 and len(users["users"]) == 1000
    status, created = request(f"/{name}/users", {"name": "redis-outage", "age": 42})
    assert status == 201
    status, users = request(f"/{name}/users")
    assert status == 200 and users["users"] == [created]
    print("PASS: MongoDB reads and writes work while Redis is stopped")
finally:
    asyncio.run(cleanup())
PY

restore_redis
trap - EXIT
docker compose exec -T pymongo-api python < scripts/check-cache.py
docker compose exec -T pymongo-api python < scripts/benchmark-cache.py
