const assert = require("node:assert/strict");

const admin = db.getSiblingDB("admin");
const config = db.getSiblingDB("config");
const application = db.getSiblingDB("somedb");
const namespace = "somedb.helloDoc";

assert.equal(admin.runCommand({hello: 1}).msg, "isdbgrid", "Connect through mongos");

for (const shard of [
    {name: "shard1RS", host: "shard1RS/shard1-1:27018,shard1-2:27018,shard1-3:27018"},
    {name: "shard2RS", host: "shard2RS/shard2-1:27018,shard2-2:27018,shard2-3:27018"},
]) {
    const current = config.shards.findOne({_id: shard.name});
    if (current) {
        assert.equal(current.host.split("/")[0], shard.name);
        assert.equal(JSON.stringify(current.host.split("/")[1].split(",").sort()),
            JSON.stringify(shard.host.split("/")[1].split(",").sort()),
            "Unexpected shard configuration");
    } else {
        assert.equal(admin.runCommand({addShard: shard.host, name: shard.name}).ok, 1);
    }
}

assert.equal(admin.runCommand({enableSharding: "somedb", primaryShard: "shard1RS"}).ok, 1);
const existing = config.collections.findOne({_id: namespace, dropped: {$ne: true}});
if (existing) {
    assert.equal(JSON.stringify(existing.key), '{"name":"hashed"}', "Collection has another shard key");
} else {
    assert.equal(application.helloDoc.countDocuments({}), 0,
        "Initial sharding expects an empty helloDoc; existing data has not been changed");
    application.helloDoc.createIndex({name: "hashed"});
    assert.equal(admin.runCommand({
        shardCollection: namespace,
        key: {name: "hashed"},
        numInitialChunks: 4,
    }).ok, 1);
}

// Стабильные _id и upsert позволяют повторить загрузку без дубликатов.
const operations = Array.from({length: 1000}, (_, i) => ({
    updateOne: {
        filter: {_id: ObjectId(i.toString(16).padStart(24, "0")), name: `ly${i}`},
        update: {$setOnInsert: {age: i}},
        upsert: true,
    },
}));
const result = application.helloDoc.bulkWrite(operations, {
    ordered: false,
    writeConcern: {w: "majority", wtimeout: 10000},
});
print(`Seed complete: inserted ${result.upsertedCount}, matched ${result.matchedCount}`);
