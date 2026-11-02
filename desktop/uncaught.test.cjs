const assert = require("node:assert/strict");
const { test } = require("node:test");
const { uncaughtHandler } = require("./uncaught.cjs");

function harness(answers) {
  const calls = { shown: [], logged: [], quit: [] };
  const handle = uncaughtHandler({
    show: async (summary) => {
      calls.shown.push(summary);
      return answers.shift() ?? true;
    },
    log: (text) => calls.logged.push(text),
    quit: (status) => calls.quit.push(status),
  });
  return { handle, calls };
}

void test("an error is logged with its stack and shown; Continue goes on, Close quits", async () => {
  const { handle, calls } = harness([true, false]);
  await handle(new TypeError("first"));
  assert.deepEqual(calls.shown, ["TypeError: first"]);
  assert.match(calls.logged[0], /Uncaught exception: TypeError: first\n\s+at /);
  assert.deepEqual(calls.quit, []);
  await handle("a rejected string");
  assert.deepEqual(calls.shown.at(-1), "Error: a rejected string");
  assert.deepEqual(calls.quit, [0]);
});

void test("the eleventh error within the forgetting time ends the app instead of showing another dialog", async () => {
  const { handle, calls } = harness([]);
  for (let i = 0; i < 10; i++) await handle(new Error(`failure ${i}`));
  assert.deepEqual(calls.quit, []);
  await handle(new Error("one too many"));
  assert.equal(calls.shown.length, 10);
  assert.deepEqual(calls.quit, [1]);
  assert.equal(calls.logged.at(-1), "Too many exceptions occurred, terminating application");
});
