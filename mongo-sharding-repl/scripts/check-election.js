const assert = require("node:assert/strict");
const previous = process.env.OLD_PRIMARY;
assert(previous, "Set OLD_PRIMARY");
const deadline = Date.now() + 120000;
while (true) {
    const hello = db.hello();
    if (hello.primary && hello.primary !== previous) {
        print(`${hello.setName}: primary changed from ${previous} to ${hello.primary}`);
        break;
    }
    assert(Date.now() < deadline, "A new primary was not elected within 120 seconds");
    sleep(1000);
}
