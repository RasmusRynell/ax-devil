import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { test } from "node:test";
import { runInNewContext } from "node:vm";
import { performance } from "node:perf_hooks";

const html = await readFile(new URL("./index.html", import.meta.url), "utf8");
const source = html.match(
  /<script id="parser" type="text\/plain">([\s\S]*?)<\/script>/,
)[1];

async function parse(payload) {
  const messages = [];
  const self = { postMessage: (message) => messages.push(message) };
  runInNewContext(source, { self, performance });
  await self.onmessage({ data: { text: async () => JSON.stringify(payload) } });
  return messages.at(-1);
}

const call = (name, ts, dur, tid = 1, pid = 1) => ({
  ph: "X",
  name,
  ts,
  dur,
  tid,
  pid,
});

test("unsorted nested calls retain correct self time, siblings, and separate processes", async () => {
  const { result } = await parse({
    traceEvents: [
      call("child", 5000, 1000),
      call("grandchild", 2500, 500),
      call("parent", 1000, 10000),
      call("child", 2000, 2000),
      call("worker", 1000, 3000, 1, 2),
      { ph: "M", name: "thread_name", tid: 1, pid: 1, args: { name: "UI" } },
    ],
  });
  const stats = (name) => result.stats[result.names.indexOf(name)];
  assert.equal(result.count, 5);
  assert.equal(result.groups.length, 2);
  assert.equal(result.groups[0].name, "UI");
  assert.equal(stats("parent").self, 7);
  assert.equal(stats("child").self, 2.5);
  assert.equal(stats("child").count, 2);
  assert.equal(stats("grandchild").self, 0.5);
  assert.equal(result.rows.length, 4);
  assert.equal(result.duration, 10);
});

test("root siblings and zero duration calls are not lost at row index zero", async () => {
  const { result } = await parse({
    traceEvents: [
      call("a", 0, 1000),
      call("b", 1000, 0),
      call("c", 1000, 1000),
    ],
  });
  assert.equal(result.count, 3);
  assert.equal(
    result.rows.reduce((n, row) => n + row.start.length, 0),
    3,
  );
  assert.equal(result.groups[0].rows.length, result.rows.length);
  for (const row of result.rows) {
    for (let i = 1; i < row.start.length; i++) {
      assert.ok(row.start[i] >= row.end[i - 1]);
    }
  }
});

test("reports overflow and skipped events without inventing duration events", async () => {
  const { result } = await parse({
    viztracer_metadata: { overflow: true },
    traceEvents: [
      call("valid", 0, 1),
      call("negative", 0, -1),
      { ph: "B", ts: 0 },
      { ph: "E", ts: 1 },
    ],
  });
  assert.equal(result.overflow, true);
  assert.equal(result.ignored, 3);
  assert.equal(result.count, 1);
});

test("rejects unsupported JSON and recordings without complete calls", async () => {
  assert.match((await parse({})).error, /traceEvents/);
  assert.match(
    (await parse({ traceEvents: [] })).error,
    /No complete duration events/,
  );
});

test("sampled recordings preserve session boundaries and leave delayed intervals empty", async () => {
  const { result } = await parse({
    format: "ax-devil-stack-samples",
    version: 1,
    interval_ms: 10,
    duration_ms: 1000,
    frames: ["root", "leaf"],
    stacks: [[0, 1]],
    threads: { 123: "UI" },
    samples: [
      [5, [["123", 0]]],
      [15, [["123", 0]]],
      [900, [["123", 0]]],
    ],
  });
  assert.equal(result.duration, 1000);
  assert.equal(result.sampled.count, 3);
  assert.equal(result.sampled.coverage, 30);
  assert.equal(result.sampled.maxGap, 875);
  assert.equal(result.stats[result.names.indexOf("root")].self, 0);
  assert.equal(result.stats[result.names.indexOf("leaf")].self, 30);
  assert.equal(result.stats[result.names.indexOf("leaf")].count, 3);
  assert.deepEqual(Array.from(result.rows[0].start), [5, 15, 900]);
  assert.deepEqual(Array.from(result.rows[0].end), [15, 25, 910]);
});

test("sampled recordings clip the final window and reject invalid stack references", async () => {
  const input = {
    format: "ax-devil-stack-samples",
    version: 1,
    interval_ms: 10,
    duration_ms: 12,
    frames: ["leaf"],
    stacks: [[0]],
    threads: {},
    samples: [[10, [["1", 0]]]],
  };
  const { result } = await parse(input);
  assert.equal(result.sampled.coverage, 2);
  assert.equal(result.stats[0].total, 2);
  input.samples[0][1][0][1] = 9;
  assert.match((await parse(input)).error, /Invalid sampled thread stack/);
});

test("flames aggregate identical paths but keep different callers and threads separate", async () => {
  const { result } = await parse({
    traceEvents: [
      call("a", 0, 10000),
      call("shared", 1000, 3000),
      call("a", 20000, 10000),
      call("shared", 21000, 4000),
      call("b", 40000, 10000),
      call("shared", 41000, 2000),
      call("a", 0, 5000, 2),
      call("shared", 1000, 1000, 2),
    ],
  });
  const child = (parent, name) =>
    result.flames[parent].children.find(
      (id) => result.names[result.flames[id].name] === name,
    );
  const root = result.groups[0].flame,
    a = child(root, "a"),
    b = child(root, "b");
  assert.equal(result.flames[root].total, 30);
  assert.equal(result.flames[a].total, 20);
  assert.equal(result.flames[a].self, 13);
  assert.equal(result.flames[a].count, 2);
  assert.equal(result.flames[child(a, "shared")].total, 7);
  assert.equal(result.flames[child(b, "shared")].total, 2);
  const second = child(result.groups[1].flame, "a");
  assert.equal(result.flames[child(second, "shared")].total, 1);
  assert.notEqual(a, second);
  const example = result.flames[child(a, "shared")];
  assert.equal(
    result.rows[example.row].end[example.index] -
      result.rows[example.row].start[example.index],
    4,
  );
});

test("sampled flame weights count covered time once per thread, not once per stack frame", async () => {
  const { result } = await parse({
    format: "ax-devil-stack-samples",
    version: 1,
    interval_ms: 10,
    duration_ms: 100,
    frames: ["root", "leaf"],
    stacks: [[0, 1]],
    threads: {},
    samples: [
      [0, [["1", 0]]],
      [10, [["1", 0]]],
      [80, [["1", 0]]],
    ],
  });
  const root = result.flames[result.groups[0].flame];
  assert.equal(root.total, 30);
  const caller = result.flames[root.children[0]],
    leaf = result.flames[caller.children[0]];
  assert.equal(caller.total, 30);
  assert.equal(caller.self, 0);
  assert.equal(leaf.total, 30);
  assert.equal(leaf.count, 3);
});
