const assert = require("node:assert/strict");
const expected = process.env.REPLICA_MEMBERS.split(",");
const isConfig = process.env.CONFIG_SERVER === "true";
const config = rs.conf();
assert.equal(config._id, process.env.REPLICA_SET);
assert.equal(config.members.length, 3);
assert(config.members.every(member => !member.arbiterOnly));
assert.equal(JSON.stringify(config.members.map(member => member.host).sort()),
    JSON.stringify([...expected].sort()));

const databases = [];
for (const host of expected) {
    const connection = new Mongo(`mongodb://${host}/?directConnection=true&readPreference=secondaryPreferred`);
    databases.push({host, database: connection.getDB(isConfig ? "config" : "somedb")});
}

const deadline = Date.now() + 120000;
while (true) {
    const status = rs.status();
    const healthy = status.members.length === 3
        && status.members.every(member => member.health === 1)
        && status.members.filter(member => member.state === 1).length === 1
        && status.members.filter(member => member.state === 2).length === 2;
    const rows = [];
    if (healthy) {
        for (const entry of databases) {
            const count = isConfig
                ? entry.database.shards.countDocuments({})
                : entry.database.helloDoc.countDocuments({});
            const row = {
                host: entry.host,
                state: status.members.find(member => member.name === entry.host).stateStr,
                documents_count: count,
            };
            if (isConfig) {
                row.sharded_collections = entry.database.collections.countDocuments({_id: "somedb.helloDoc"});
            }
            rows.push(row);
        }
        const identical = rows.every(row => row.documents_count > 0
            && row.documents_count === rows[0].documents_count);
        const metadataReady = !isConfig || rows.every(row => row.documents_count === 2 && row.sharded_collections === 1);
        if (identical && metadataReady) {
            printjson({replica_set: config._id, replicas_count: 3, members: rows});
            break;
        }
    }
    assert(Date.now() < deadline, `${config._id}: replicas did not synchronize within 120 seconds`);
    sleep(1000);
}
