const assert = require("node:assert/strict");

const setName = process.env.REPLICA_SET;
const memberHosts = (process.env.REPLICA_MEMBERS || "").split(",");
assert(setName && memberHosts.length === 3, "Configure REPLICA_SET and three REPLICA_MEMBERS");
const isConfig = process.env.CONFIG_SERVER === "true";

let initialized = false;
try {
    const status = rs.status();
    assert.equal(status.set, setName, "Unexpected replica set name");
    const config = rs.conf();
    assert.equal(JSON.stringify(config.members.map(member => member.host).sort()),
        JSON.stringify([...memberHosts].sort()), "Existing replica set has different members");
    assert.equal(Boolean(config.configsvr), isConfig, "Unexpected config server role");
    initialized = true;
} catch (error) {
    if (error.code !== 94) { // NotYetInitialized
        throw error;
    }
}

if (!initialized) {
    const config = {
        _id: setName,
        members: memberHosts.map((host, i) => ({_id: i, host})),
    };
    if (isConfig) {
        config.configsvr = true;
    }
    assert.equal(rs.initiate(config).ok, 1);
}

const deadline = Date.now() + 120000;
while (true) {
    const members = rs.status().members;
    const ready = members.length === 3
        && members.every(member => member.health === 1)
        && members.filter(member => member.state === 1).length === 1
        && members.filter(member => member.state === 2).length === 2;
    if (ready) {
        print(`${setName}: 1 primary + 2 secondary ready`);
        break;
    }
    assert(Date.now() < deadline, `${setName}: replica set is not ready within 120 seconds`);
    sleep(1000);
}
