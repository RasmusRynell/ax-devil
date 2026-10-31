// @vitest-environment node
import { describe, expect, it } from "vite-plus/test";
import { fakeWorkspace, pending, resolved, video } from "./fakeEvents";

function setup() {
  const workspace = fakeWorkspace();
  workspace.store.start();
  return workspace;
}

describe("viewers open once per item", () => {
  it("opens a viewer for an item the backend marks open, even before it resolves, and shows the newest", () => {
    const { store, source } = setup();
    source().send([video("a", true, pending)]);
    expect(store.getSnapshot()).toMatchObject({ viewers: ["a"], active: "a" });
    source().send([video("a", true, resolved("a")), video("b", true, pending)]);
    expect(store.getSnapshot()).toMatchObject({ viewers: ["a", "b"], active: "b" });
  });

  it("opens no viewer for an item that was not marked open", () => {
    const { store, source } = setup();
    source().send([video("a", false, resolved("a"))]);
    expect(store.getSnapshot()).toMatchObject({ viewers: [], active: null });
  });

  it("does not reopen a closed viewer when the backend repeats the flag, also after reconnecting", () => {
    const { store, source, sources, retries } = setup();
    source().send([video("a", true, resolved("a"))]);
    store.close("a");
    expect(store.getSnapshot()).toMatchObject({ viewers: [], active: null });
    source().send([video("a", true, resolved("a"))]);
    source().fail(2);
    retries[0]();
    expect(sources).toHaveLength(2);
    source().send([video("a", true, resolved("a"))]);
    expect(store.getSnapshot().viewers).toEqual([]);
  });

  it("falls back to another viewer when the active one is closed or its item is gone", () => {
    const { store, source } = setup();
    source().send([video("a", true, resolved("a")), video("b", true, resolved("b"))]);
    store.activate("a");
    store.close("a");
    expect(store.getSnapshot()).toMatchObject({ viewers: ["b"], active: "b" });
    source().send([video("a", false, resolved("a"))]);
    expect(store.getSnapshot()).toMatchObject({ viewers: [], active: null });
  });
});

describe("what a viewer remembers", () => {
  it("survives workspace updates and a reconnect, and a closed viewer forgets it", () => {
    const { store, source, retries } = setup();
    source().send([video("a", true, resolved("a")), video("b", true, resolved("b"))]);
    store.remember("a", { frame: 12 });
    source().send([video("a", true, resolved("a")), video("b", true, resolved("b"))]);
    source().fail(2);
    retries[0]();
    source().send([video("a", true, resolved("a")), video("b", true, resolved("b"))]);
    expect(store.remembered("a")).toEqual({ frame: 12 });
    store.close("a");
    expect(store.remembered("a")).toBeUndefined();
    store.remember("a", { frame: 99 });
    expect(store.remembered("a")).toBeUndefined();
  });
});

describe("connection", () => {
  it("reports a lost connection and opens a new one after the backend refused the old", () => {
    const { store, source, sources, retries } = setup();
    source().send([]);
    expect(store.getSnapshot().connection).toBe("open");
    source().fail(0);
    expect(store.getSnapshot().connection).toBe("lost");
    expect(retries).toHaveLength(0);
    source().fail(2);
    expect(retries).toHaveLength(1);
    retries[0]();
    expect(sources).toHaveLength(2);
    source().send([]);
    expect(store.getSnapshot().connection).toBe("open");
  });
});
