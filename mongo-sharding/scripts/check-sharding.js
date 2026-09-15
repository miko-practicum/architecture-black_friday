const assert = require("node:assert/strict");

const application = db.getSiblingDB("somedb");
const config = db.getSiblingDB("config");
const shards = db.adminCommand({listShards: 1});
assert.equal(shards.ok, 1);
assert.equal(JSON.stringify(shards.shards.map(shard => shard._id).sort()), '["shard1RS","shard2RS"]');
assert.equal(JSON.stringify(config.collections.findOne({_id: "somedb.helloDoc"}).key), '{"name":"hashed"}');

const total = application.helloDoc.countDocuments({});
const rows = application.helloDoc.aggregate([{$collStats: {count: {}}}]).toArray();
const byShard = Object.fromEntries(rows.map(row => [row.shard, Number(row.count)]));
assert(total >= 1000, "Expected at least 1000 documents");
assert(byShard.shard1RS > 0, "shard1RS has no documents");
assert(byShard.shard2RS > 0, "shard2RS has no documents");
assert.equal(total, byShard.shard1RS + byShard.shard2RS, "Shard counts must sum to total on an idle cluster");
printjson({database: "somedb", collection: "helloDoc", total, documents_per_shard: byShard});
