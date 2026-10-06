import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { test } from "node:test";
import { runInNewContext } from "node:vm";
import { performance } from "node:perf_hooks";

const html = await readFile(new URL("./index.html", import.meta.url), "utf8");
const parser = html.match(
  /<script id="parser" type="text\/plain">([\s\S]*?)<\/script>/,
)[1];
const apiSource = html.match(/<script id="agent-api">([\s\S]*?)<\/script>/)[1];

async function fixture() {
  const messages = [];
  const self = { postMessage: (message) => messages.push(message) };
  runInNewContext(parser, { self, performance });
  await self.onmessage({
    data: {
      text: async () =>
        JSON.stringify({
          traceEvents: [
            { ph: "X", tid: 1, pid: 1, name: "parent", ts: 0, dur: 10000 },
            ...Array.from({ length: 100 }, (_, i) => ({
              ph: "X",
              tid: 1,
              pid: 1,
              name: `child-${i}`,
              ts: i * 100,
              dur: 90,
            })),
            { ph: "X", tid: 2, pid: 1, name: "other-thread", ts: 0, dur: 9000 },
          ],
        }),
    },
  });
  const trace = messages.at(-1).result;
  const state = {
    trace,
    recordingId: "recording-1",
    filename: "fixture.json",
    mode: "timeline",
    selection: { row: 0, index: 0 },
    flameSelection: null,
    highlight: -1,
    viewStart: 0,
    viewEnd: 10,
    flameFocus: new Map(),
    collapsed: new Set(),
    searchText: "",
    loading: false,
  };
  const context = { setTimeout };
  runInNewContext(`${apiSource}\nglobalThis.API=TraceAgentAPI;`, context);
  const navigations = [];
  return {
    state,
    navigations,
    api: new context.API(
      () => state,
      (args) => {
        navigations.push(args);
        if (args.item_id?.startsWith("timeline:")) {
          const [, row, index] = args.item_id.split(":").map(Number);
          state.selection = { row, index };
        }
        if (args.start_ms != null && args.end_ms != null) {
          state.viewStart = args.start_ms;
          state.viewEnd = args.end_ms;
        }
      },
    ),
  };
}

test("live selection and thread discovery stay bounded", async () => {
  const { api, state } = await fixture();
  const view = await api.dispatch("get_view", { limit: 1 });
  assert.equal(view.selection.name, "parent");
  assert.equal(view.threads.length, 1);
  assert.equal(view.next_offset, 1);
  state.selection = { row: 1, index: 4 };
  assert.equal((await api.dispatch("get_view")).selection.name, "child-4");
});

test("timeline search filters intervals and pages without navigating", async () => {
  const { api, navigations } = await fixture();
  const args = {
    recording_id: "recording-1",
    view: "timeline",
    query: "child",
    start_ms: 2,
    end_ms: 3,
    sort: "start",
    limit: 3,
  };
  const first = await api.dispatch("search", args),
    second = await api.dispatch("search", { ...args, offset: 3 });
  assert.equal(first.matched, 10);
  assert.equal(first.items.length, 3);
  assert.equal(first.next_offset, 3);
  assert.equal(first.items[0].name, "child-20");
  assert.equal(second.items[0].name, "child-23");
  assert.equal(navigations.length, 0);
});

test("flame searches and direct children support bounded independent exploration", async () => {
  const { api } = await fixture();
  const found = await api.dispatch("search", {
    recording_id: "recording-1",
    view: "flame",
    thread_id: "1:1",
    query: "parent",
  });
  assert.equal(found.items.length, 1);
  const first = await api.dispatch("inspect", {
    recording_id: "recording-1",
    item_id: found.items[0].item_id,
  });
  assert.equal(first.children.length, 5);
  assert.equal(first.next_offset, 5);
  const details = await api.dispatch("inspect", {
    recording_id: "recording-1",
    item_id: found.items[0].item_id,
    limit: 5,
    offset: 5,
  });
  assert.equal(details.children.length, 5);
  assert.equal(details.child_count, 100);
  assert.equal(details.next_offset, 10);
  assert.equal(details.parents.length, 1);
  const timeline = await api.dispatch("inspect", {
    recording_id: "recording-1",
    item_id: found.items[0].representative_id,
    limit: 4,
  });
  assert.equal(timeline.child_count, 100);
  assert.equal(timeline.children.length, 4);
  const child = await api.dispatch("inspect", {
    recording_id: "recording-1",
    item_id: timeline.children[0].item_id,
  });
  assert.equal(child.parents[0].name, "parent");
});

test("stale IDs, invalid limits, and ambiguous flame ranges fail before navigation", async () => {
  const { api, state, navigations } = await fixture();
  await assert.rejects(
    api.dispatch("focus", { recording_id: "old", item_id: "flame:1" }),
    /stale/,
  );
  await assert.rejects(
    api.dispatch("search", {
      recording_id: "recording-1",
      view: "flame",
      start_ms: 1,
    }),
    /whole recording/,
  );
  await assert.rejects(
    api.dispatch("search", { recording_id: "recording-1", limit: 500 }),
    /limit/,
  );
  await assert.rejects(
    api.dispatch("focus", {
      recording_id: "recording-1",
      start_ms: 0,
      end_ms: 100,
    }),
    /within/,
  );
  state.loading = true;
  await assert.rejects(
    api.dispatch("focus", { recording_id: "recording-1", item_id: "flame:1" }),
    /loading/,
  );
  assert.equal(navigations.length, 0);
});

test("focus is explicit and returns live confirmation", async () => {
  const { api, navigations } = await fixture();
  const result = await api.dispatch("focus", {
    recording_id: "recording-1",
    item_id: "timeline:1:4",
  });
  assert.equal(result.applied, true);
  assert.equal(navigations.length, 1);
  assert.equal(result.selection.name, "child-4");
  assert.equal(result.selection.item_id, "timeline:1:4");
  const interval = await api.dispatch("focus", {
    recording_id: "recording-1",
    start_ms: 2,
    end_ms: 3,
  });
  assert.equal(interval.applied, true);
  assert.equal(navigations.length, 2);
  assert.deepEqual(Array.from(interval.visible_range_ms), [2, 3]);
});
