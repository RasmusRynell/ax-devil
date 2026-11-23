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

void test("after Close, errors already waiting and new ones open no more dialogs", async () => {
  const { handle, calls } = harness([false]);
  const waiting = [handle(new Error("first")), handle(new Error("second"))];
  await Promise.all(waiting);
  await handle(new Error("third"));
  assert.deepEqual(calls.shown, ["Error: first"]);
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

void test("errors wait for their turn: one dialog at a time, and the waiting ones count toward the limit", async () => {
  const answers = [];
  const calls = { shown: 0, quit: [] };
  const handle = uncaughtHandler({
    show: () => {
      calls.shown++;
      return new Promise((resolve) => answers.push(resolve));
    },
    log: () => {},
    quit: (status) => calls.quit.push(status),
  });
  const handled = Array.from({ length: 10 }, (_, i) => handle(new Error(`failure ${i}`)));
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(calls.shown, 1);
  await handle(new Error("one too many"));
  assert.deepEqual(calls.quit, [1]);
  for (let i = 0; i < 10; i++) {
    answers[i]?.(true);
    await new Promise((resolve) => setImmediate(resolve));
  }
  await Promise.all(handled);
  assert.equal(calls.shown, 10);
});
