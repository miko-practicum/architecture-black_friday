"""Cache integration checks; run inside the pymongo-api container."""

import asyncio
import json
import os
import time
import unittest
import uuid
from urllib.request import Request, urlopen

from motor.motor_asyncio import AsyncIOMotorClient
from redis import Redis


def request(path, body=None):
    data = None if body is None else json.dumps(body).encode()
    req = Request(f"http://localhost:8080{path}", data=data,
                  headers={"Content-Type": "application/json"})
    with urlopen(req, timeout=15) as response:
        return response.status, json.load(response)


class CacheChecks(unittest.TestCase):
    def setUp(self):
        self.redis = Redis.from_url(os.environ["REDIS_URL"])
        self.collections = [f"cache_check_{uuid.uuid4().hex}" for _ in range(2)]
        self.keys = [f"api:cache:somedb:users:v1:{name}" for name in self.collections]

    def tearDown(self):
        async def cleanup():
            client = AsyncIOMotorClient(os.environ["MONGODB_URL"])
            try:
                for name in self.collections:
                    await client.somedb.drop_collection(name)
            finally:
                client.close()

        try:
            asyncio.run(cleanup())
        finally:
            self.redis.delete(*self.keys)
            self.redis.close()

    def test_cache_enabled_and_cluster_preserved(self):
        _, root = request("/")
        self.assertTrue(root["cache_enabled"])
        self.assertEqual(root["replicas_per_shard"], {"shard1RS": 3, "shard2RS": 3})
        self.assertGreaterEqual(root["collections"]["helloDoc"]["documents_count"], 1000)

    def test_cache_roundtrip_and_expiration(self):
        path = f"/{self.collections[0]}/users"
        _, created = request(path, {"name": "ttl-probe", "age": 42})
        _, cold = request(path)
        self.assertEqual(cold["users"], [created])
        self.assertGreater(self.redis.ttl(self.keys[0]), 0)
        self.assertLessEqual(self.redis.ttl(self.keys[0]), 60)
        hits = self.redis.info("stats")["keyspace_hits"]
        _, warm = request(path)
        self.assertEqual(warm, cold)
        self.assertGreater(self.redis.info("stats")["keyspace_hits"], hits)
        # Expire only the test key, so this check does not have to wait 60 seconds.
        self.redis.pexpire(self.keys[0], 1)
        time.sleep(0.02)
        self.assertFalse(self.redis.exists(self.keys[0]))
        _, refreshed = request(path)
        self.assertEqual(refreshed, cold)
        self.assertGreater(self.redis.ttl(self.keys[0]), 0)

    def test_write_invalidates_only_its_collection(self):
        paths = [f"/{name}/users" for name in self.collections]
        for path in paths:
            _, empty = request(path)
            self.assertEqual(empty, {"users": []})
        self.assertEqual(self.redis.exists(*self.keys), 2)
        status, created = request(paths[0], {"name": "new-user", "age": 25})
        self.assertEqual(status, 201)
        self.assertFalse(self.redis.exists(self.keys[0]))
        self.assertTrue(self.redis.exists(self.keys[1]))
        _, updated = request(paths[0])
        self.assertEqual(updated["users"], [created])
        _, unchanged = request(paths[1])
        self.assertEqual(unchanged, {"users": []})


if __name__ == "__main__":
    unittest.main(verbosity=2)
