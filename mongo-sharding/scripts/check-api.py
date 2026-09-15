"""Integration checks; run with Python inside the pymongo-api container."""

import asyncio
import json
import os
import unittest
import uuid
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from motor.motor_asyncio import AsyncIOMotorClient


def request(path, body=None):
    data = None if body is None else json.dumps(body).encode()
    req = Request(
        f"http://localhost:8080{path}",
        data=data,
        headers={"Content-Type": "application/json"},
    )
    with urlopen(req, timeout=15) as response:
        return response.status, json.load(response)


async def remove_probe(name):
    client = AsyncIOMotorClient(os.environ["MONGODB_URL"])
    try:
        await client["somedb"]["helloDoc"].delete_one({"name": name})
    finally:
        client.close()


class ShardingAPI(unittest.TestCase):
    def test_root_and_counts(self):
        status, root = request("/")
        self.assertEqual(status, 200)
        self.assertEqual(root["mongo_topology_type"], "Sharded")
        self.assertTrue(root["mongo_is_mongos"])
        self.assertEqual(set(root["shards"]), {"shard1RS", "shard2RS"})
        statistics = root["collections"]["helloDoc"]
        self.assertGreaterEqual(statistics["documents_count"], 1000)
        self.assertEqual(set(statistics["documents_per_shard"]), set(root["shards"]))
        self.assertTrue(all(count > 0 for count in statistics["documents_per_shard"].values()))
        self.assertEqual(sum(statistics["documents_per_shard"].values()), statistics["documents_count"])
        _, count = request("/helloDoc/count")
        self.assertEqual(count["items_count"], statistics["documents_count"])
        self.assertEqual(count["documents_per_shard"], statistics["documents_per_shard"])

    def test_seed_data(self):
        _, result = request("/helloDoc/users")
        self.assertEqual(len(result["users"]), 1000)
        _, user = request("/helloDoc/users/ly42")
        self.assertEqual(user["name"], "ly42")
        self.assertEqual(user["age"], 42)

    def test_docs(self):
        with urlopen("http://localhost:8080/docs", timeout=10) as response:
            self.assertEqual(response.status, 200)

    def test_missing_collection(self):
        with self.assertRaises(HTTPError) as caught:
            request("/missing_collection_for_sharding_check/count")
        self.assertEqual(caught.exception.code, 404)

    def test_create_user_through_mongos(self):
        name = f"sharding-check-{uuid.uuid4().hex}"
        _, before = request("/helloDoc/count")
        try:
            status, user = request("/helloDoc/users", {"name": name, "age": 25})
            self.assertEqual(status, 201)
            self.assertEqual(user["name"], name)
            _, fetched = request(f"/helloDoc/users/{name}")
            self.assertEqual(fetched["id"], user["id"])
            _, after = request("/helloDoc/count")
            self.assertEqual(after["items_count"], before["items_count"] + 1)
            self.assertEqual(sum(after["documents_per_shard"].values()), after["items_count"])
        finally:
            asyncio.run(remove_probe(name))
        _, restored = request("/helloDoc/count")
        self.assertEqual(restored["items_count"], before["items_count"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
