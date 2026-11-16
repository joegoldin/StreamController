// Execute the generated KWin script with the compositor's signal surface.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

function signal() {
    const listeners = [];
    return {
        connect(listener) { listeners.push(listener); },
        emit(...args) { listeners.forEach(listener => listener(...args)); },
    };
}
function window(id, title) {
    return { internalId: id, resourceClass: "zen", caption: title,
             normalWindow: true, captionChanged: signal() };
}
const first = window("first", "Video");
let windows = [first];
const messages = [];
const workspace = {
    activeWindow: first, windowList: () => windows,
    windowActivated: signal(), windowAdded: signal(), windowRemoved: signal(),
};
vm.runInNewContext(fs.readFileSync(process.argv[2], "utf8"), {
    workspace,
    callDBus(destination, path, iface, method, rows, active) {
        messages.push({ rows: JSON.parse(rows), active });
    },
});
assert.equal(messages.length, 1);
assert.equal(messages.at(-1).rows[0].title, "Video");
first.caption = "";
first.captionChanged.emit();
assert.equal(messages.at(-1).rows[0].title, "");
const second = window("second", "New window");
windows.push(second);
workspace.windowAdded.emit(second);
workspace.activeWindow = second;
workspace.windowActivated.emit(second);
assert.equal(messages.at(-1).active, "second");
second.caption = "Updated";
second.captionChanged.emit();
assert.equal(messages.at(-1).rows[1].title, "Updated");
windows = [first];
workspace.activeWindow = first;
workspace.windowRemoved.emit(second);
assert.equal(messages.at(-1).rows.length, 1);
assert.equal(messages.at(-1).active, "first");
console.log("KWin script handles initial state, focus, titles, addition and removal.");
