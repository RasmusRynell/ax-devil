import { afterEach, describe, expect, it, vi } from "vite-plus/test";
import type { LaneReaderInfo } from "../generated/api";
import {
  createBus,
  deliverCommand,
  onFrameLoad,
  type Binding,
  type Bus,
  type BusHost,
} from "./bus";
import type { PluginView } from "./listing";

const options = (kind: "File" | "Folder" | "Text") => [
  {
    key: "out",
    label: "Out",
    kind: kind === "File" ? { kind, extensions: [] } : { kind },
    required: false,
  },
];
const reader = (id: string, kind: "decoder" | "task", form = options("Text")): LaneReaderInfo => ({
  id,
  kind,
  name: id,
  lanes: [["a", "A"]],
  video: false,
  options: form,
});
const plugin: PluginView = {
  plugin: "acme",
  view: { id: "review", title: "Review", place: "sidebar", url: "/views/t/index.html" },
  readers: [
    reader("acme_iou", "decoder"),
    reader("paths", "decoder", options("Folder")),
    reader("coco", "task", options("File")),
    reader("plain", "task"),
  ],
  commands: [{ id: "acme.export", title: "Export", on: "lane", runs: "plain" }],
};
const binding = (item = "i"): Binding => ({
  viewer: 1,
  item,
  entry: "e",
  lanes: [{ slot: "x", label: "X", basis: null, complete: true }],
  selection: null,
  theme: "dark",
});

const buses: Bus[] = [];
afterEach(() => buses.splice(0).forEach((bus) => bus.close()));

/** A bus bound once (epoch 1), and a way for the view to call it. */
function open(runTask: BusHost["runTask"] = () => Promise.resolve()) {
  const host = {
    viewerSet: vi.fn<BusHost["viewerSet"]>(() => true),
    readScenes: vi.fn<BusHost["readScenes"]>(() => Promise.resolve("")),
    setLane: vi.fn<BusHost["setLane"]>(() => Promise.resolve()),
    runTask: vi.fn<BusHost["runTask"]>(runTask),
    runKey: vi.fn<BusHost["runKey"]>(),
  };
  const bus = createBus(plugin, host);
  buses.push(bus);
  bus.rebind(binding());
  const replies = new Map<number, (reply: Record<string, unknown>) => void>();
  bus.port.onmessage = ({ data }) => replies.get((data as { id: number }).id)?.(data);
  let next = 0;
  const call = (name: string, args: unknown, epoch = 1) =>
    new Promise<Record<string, unknown>>((resolve) => {
      const id = (next += 1);
      replies.set(id, resolve);
      bus.port.postMessage({ type: "call", id, call: name, epoch, args });
    });
  return { host, bus, call };
}

describe("the bus", () => {
  it("answers a call of another epoch stale and calls nothing", async () => {
    const { host, bus, call } = open();
    bus.rebind(binding("j"));
    const lane = {
      slot: "acme.iou",
      handler: "acme_iou",
      lanes: { a: "x" },
      options: {},
      label: "L",
    };
    expect(await call("lanes.set", lane, 1)).toMatchObject({ ok: false, error: "stale" });
    expect(await call("task.run", { handler: "plain", lanes: {} }, 1)).toMatchObject({
      error: "stale",
    });
    expect(host.setLane).not.toHaveBeenCalled();
    expect(host.runTask).not.toHaveBeenCalled();
    expect(await call("lanes.set", lane, 2)).toMatchObject({ ok: true });
    expect(host.setLane).toHaveBeenCalledOnce();
  });

  it("lets a view set only its own slots, with its own handlers and no path options", async () => {
    const { host, call } = open();
    const set = (change: object) =>
      call("lanes.set", {
        slot: "acme.iou",
        handler: "acme_iou",
        lanes: {},
        options: {},
        label: "L",
        ...change,
      });
    expect(await set({ slot: "other.iou" })).toMatchObject({ error: "refused" });
    expect(await set({ slot: "video.mp4" })).toMatchObject({ error: "refused" });
    expect(await set({ handler: "theirs" })).toMatchObject({ error: "refused" });
    expect(await set({ handler: "plain" })).toMatchObject({ error: "refused" });
    expect(await set({ handler: "paths", options: { out: "/tmp" } })).toMatchObject({
      error: "refused",
    });
    expect(
      await call("task.run", { handler: "coco", lanes: {}, options: { out: "/tmp" } }),
    ).toMatchObject({
      error: "refused",
    });
    expect(await set({ options: { big: "x".repeat(256 << 10) } })).toMatchObject({
      error: "too_large",
    });
    expect(await call("lanes.set", { slot: "other.iou", remove: true })).toMatchObject({
      error: "refused",
    });
    expect(host.setLane).not.toHaveBeenCalled();
    expect(await set({})).toMatchObject({ ok: true });
    expect(await call("lanes.set", { slot: "acme.iou", remove: true })).toMatchObject({ ok: true });
    expect(host.setLane).toHaveBeenLastCalledWith(expect.anything(), "acme.iou", null);
  });

  it("lets a lane read only a lane the viewer lists or this view set before", async () => {
    const { host, call } = open();
    const set = (slot: string, input: string) =>
      call("lanes.set", { slot, handler: "acme_iou", lanes: { a: input }, label: "L" });
    expect(await set("acme.first", "nowhere")).toMatchObject({ ok: false, error: "unknown_slot" });
    expect(host.setLane).not.toHaveBeenCalled();
    expect(await set("acme.first", "x")).toMatchObject({ ok: true });
    expect(await set("acme.second", "acme.first")).toMatchObject({ ok: true });
    expect(await call("lanes.set", { slot: "acme.first", remove: true })).toMatchObject({
      ok: true,
    });
    expect(await set("acme.third", "acme.first")).toMatchObject({ error: "unknown_slot" });
  });

  it("runs a key only for a view-scope command or the plugin's own", async () => {
    const { host, bus } = open();
    const run = host.runKey;
    bus.port.postMessage({ type: "key", key: "Ctrl+Shift+K", epoch: 1 });
    await vi.waitFor(() => expect(run).toHaveBeenCalledOnce());
    const allowed = run.mock.calls[0][2];
    expect([allowed("zoom-in"), allowed("acme.export")]).toEqual([true, true]);
    expect([allowed("open-workspace"), allowed("settings"), allowed("other.export")]).toEqual([
      false,
      false,
      false,
    ]);
  });

  it("runs a key for the viewer the view is bound to and drops one of another epoch", async () => {
    const { host, bus } = open();
    const other = { ...binding("j"), entry: "f" };
    bus.rebind(other);
    bus.port.postMessage({ type: "key", key: "Space", epoch: 1 });
    bus.port.postMessage({ type: "key", key: "Space" });
    bus.port.postMessage({ type: "key", key: "K", epoch: 2 });
    await vi.waitFor(() => expect(host.runKey).toHaveBeenCalledOnce());
    expect(host.runKey).toHaveBeenCalledWith(other, "K", expect.any(Function));
  });

  it("cancels a running task by its call id", async () => {
    const { host, call, bus } = open(
      (_binding, _run, _progress, signal) =>
        new Promise((_done, fail) => signal.addEventListener("abort", () => fail(new Error("x")))),
    );
    const running = call("task.run", { handler: "plain", lanes: {} });
    await vi.waitFor(() => expect(host.runTask).toHaveBeenCalled());
    bus.port.postMessage({ type: "call", id: 99, call: "cancel", args: { id: 1 } });
    expect(await running).toMatchObject({ ok: false, error: "cancelled" });
  });

  it("ends a lane or scenes call that cannot be interrupted at its cancel", async () => {
    const { host, call, bus } = open();
    host.setLane.mockImplementation(() => new Promise(() => {}));
    host.readScenes.mockImplementation(() => new Promise(() => {}));
    const set = call("lanes.set", {
      slot: "acme.iou",
      handler: "acme_iou",
      lanes: {},
      label: "L",
    });
    const read = call("scenes.read", { slot: "a", from: 0, count: 1 });
    await vi.waitFor(() => expect(host.readScenes).toHaveBeenCalled());
    bus.port.postMessage({ type: "call", id: 98, call: "cancel", args: { id: 1 } });
    bus.port.postMessage({ type: "call", id: 99, call: "cancel", args: { id: 2 } });
    expect(await set).toMatchObject({ ok: false, error: "cancelled" });
    expect(await read).toMatchObject({ ok: false, error: "cancelled" });
  });
});

describe("the frame", () => {
  it("gives its port on the first load and ends the bus when the view left its page", () => {
    const give = vi.fn();
    const left = vi.fn();
    const load = onFrameLoad(
      () => createBus(plugin, { runKey: vi.fn() } as unknown as BusHost),
      give,
      left,
    );
    load();
    expect(give).toHaveBeenCalledOnce();
    expect(deliverCommand("acme.review", "acme.export")).toBe(true);
    load();
    expect(left).toHaveBeenCalledOnce();
    expect(give).toHaveBeenCalledOnce();
    expect(deliverCommand("acme.review", "acme.export")).toBe(false);
  });
});
