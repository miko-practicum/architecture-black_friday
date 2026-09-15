import logging
import logging.config
import os
import time
from typing import List, Optional

import motor.motor_asyncio
from bson import ObjectId
from fastapi import Body, FastAPI, HTTPException, status
from fastapi_cache import FastAPICache
from fastapi_cache.backends.redis import RedisBackend
from fastapi_cache.decorator import cache
from logmiddleware import RouterLoggingMiddleware, logging_config
from pydantic import BaseModel, ConfigDict, EmailStr, Field
from pydantic.functional_validators import BeforeValidator
from redis import asyncio as aioredis
from typing_extensions import Annotated

# Configure JSON logging
logging.config.dictConfig(logging_config)
logger = logging.getLogger(__name__)

app = FastAPI()
app.add_middleware(
    RouterLoggingMiddleware,
    logger=logger,
)

DATABASE_URL = os.environ["MONGODB_URL"]
DATABASE_NAME = os.environ["MONGODB_DATABASE_NAME"]
REDIS_URL = os.getenv("REDIS_URL", None)


def nocache(*args, **kwargs):
    def decorator(func):
        return func

    return decorator


if REDIS_URL:
    cache = cache
else:
    cache = nocache


client = motor.motor_asyncio.AsyncIOMotorClient(DATABASE_URL)
db = client[DATABASE_NAME]

# Represents an ObjectId field in the database.
# It will be represented as a `str` on the model so that it can be serialized to JSON.
PyObjectId = Annotated[str, BeforeValidator(str)]


@app.on_event("startup")
async def startup():
    if REDIS_URL:
        redis = aioredis.from_url(REDIS_URL, encoding="utf8", decode_responses=True)
        FastAPICache.init(RedisBackend(redis), prefix="api:cache")


class UserModel(BaseModel):
    """
    Container for a single user record.
    """

    id: Optional[PyObjectId] = Field(alias="_id", default=None)
    age: int = Field(...)
    name: str = Field(...)


class UserCollection(BaseModel):
    """
    A container holding a list of `UserModel` instances.
    """

    users: List[UserModel]


async def collection_statistics(collection_name: str, shard_names: list[str]):
    collection = db.get_collection(collection_name)
    total = await collection.count_documents({})
    by_shard = {name: 0 for name in shard_names}
    if shard_names:
        async for row in collection.aggregate([{"$collStats": {"count": {}}}]):
            by_shard[row["shard"]] = row["count"]
    return {"documents_count": total, "documents_per_shard": by_shard}


async def cluster_shards():
    response = await client.admin.command("listShards")
    return {shard["_id"]: shard["host"] for shard in response["shards"]}


def replication_summary(shards):
    # listShards returns the configured members; this is not a health check.
    replica_sets = {}
    for name, connection in shards.items():
        set_name, hosts = connection.split("/", 1)
        members = sorted(set(hosts.split(",")))
        replica_sets[name] = {"replica_set": set_name, "members": members}
    counts = {name: len(info["members"]) for name, info in replica_sets.items()}
    return {
        "replicas_per_shard": counts,
        "data_replicas_count": sum(counts.values()),
        "replica_sets": replica_sets,
    }


@app.get("/")
async def root():
    hello = await client.admin.command("hello")
    shards = await cluster_shards()
    collection_names = await db.list_collection_names()
    collections = {}
    for collection_name in collection_names:
        collections[collection_name] = await collection_statistics(
            collection_name, list(shards)
        )

    cache_enabled = False
    if REDIS_URL:
        cache_enabled = FastAPICache.get_enable()

    return {
        "mongo_topology_type": client.topology_description.topology_type_name,
        "mongo_replicaset_name": hello.get("setName"),
        "mongo_db": DATABASE_NAME,
        "read_preference": str(client.read_preference),
        "mongo_nodes": client.nodes,
        "mongo_primary_host": hello.get("primary"),
        "mongo_secondary_hosts": [],
        "mongo_is_primary": hello.get("isWritablePrimary", False),
        "mongo_is_mongos": hello.get("msg") == "isdbgrid",
        "collections": collections,
        "shards": shards,
        **replication_summary(shards),
        "cache_enabled": cache_enabled,
        "status": "OK",
    }


@app.get("/{collection_name}/count")
async def collection_count(collection_name: str):
    if collection_name not in await db.list_collection_names():
        raise HTTPException(status_code=404, detail=f"Collection {collection_name} not found")
    shards = await cluster_shards()
    statistics = await collection_statistics(collection_name, list(shards))
    return {
        "status": "OK",
        "mongo_db": DATABASE_NAME,
        "items_count": statistics["documents_count"],
        "documents_per_shard": statistics["documents_per_shard"],
        **replication_summary(shards),
    }


@app.get(
    "/{collection_name}/users",
    response_description="List all users",
    response_model=UserCollection,
    response_model_by_alias=False,
)
@cache(expire=60 * 1)
async def list_users(collection_name: str):
    """
    List all of the user data in the database.
    The response is unpaginated and limited to 1000 results.
    """
    time.sleep(1)
    collection = db.get_collection(collection_name)
    return UserCollection(users=await collection.find().to_list(1000))


@app.get(
    "/{collection_name}/users/{name}",
    response_description="Get a single user",
    response_model=UserModel,
    response_model_by_alias=False,
)
async def show_user(collection_name: str, name: str):
    """
    Get the record for a specific user, looked up by `name`.
    """

    collection = db.get_collection(collection_name)
    if (user := await collection.find_one({"name": name})) is not None:
        return user

    raise HTTPException(status_code=404, detail=f"User {name} not found")


@app.post(
    "/{collection_name}/users",
    response_description="Add new user",
    response_model=UserModel,
    status_code=status.HTTP_201_CREATED,
    response_model_by_alias=False,
)
async def create_user(collection_name: str, user: UserModel = Body(...)):
    """
    Insert a new user record.

    A unique `id` will be created and provided in the response.
    """
    collection = db.get_collection(collection_name)
    new_user = await collection.insert_one(
        user.model_dump(by_alias=True, exclude=["id"])
    )
    created_user = await collection.find_one({"_id": new_user.inserted_id})
    return created_user
