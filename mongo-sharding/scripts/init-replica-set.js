const assert = require("node:assert/strict");

const setName = process.env.REPLICA_SET;
const memberHost = process.env.MEMBER_HOST;
assert(setName && memberHost, "REPLICA_SET and MEMBER_HOST must be configured");

let initialized = false;
try {
    const status = rs.status();
    assert.equal(status.set, setName, "Unexpected replica set name");
    initialized = true;
} catch (error) {
    if (error.code !== 94) { // NotYetInitialized
        throw error;
    }
}

if (!initialized) {
    const config = {_id: setName, members: [{_id: 0, host: memberHost}]};
    if (process.env.CONFIG_SERVER === "true") {
        config.configsvr = true;
    }
    assert.equal(rs.initiate(config).ok, 1);
}

const deadline = Date.now() + 60000;
while (!db.hello().isWritablePrimary) {
    assert(Date.now() < deadline, `${setName}: primary was not elected within 60 seconds`);
    sleep(1000);
}
print(`${setName}: primary is ready (${memberHost})`);
