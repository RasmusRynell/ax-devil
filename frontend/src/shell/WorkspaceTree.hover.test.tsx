import { afterEach, beforeEach, describe, expect, it, vi } from "vite-plus/test";
import { act, cleanup, fireEvent, screen } from "@testing-library/react";
import { scenario } from "../session/fakeScenario";
import { FakeSocket } from "../session/fakeSession";
import { KINDS, labels, renderTree, TREE, useTreeHarness } from "./WorkspaceTree.fixture";

useTreeHarness();

describe("hover card", () => {
  const bitmaps: { width: number; height: number; close: ReturnType<typeof vi.fn> }[] = [];
  const requests: { signal: AbortSignal; tag: string | null }[] = [];
  const found = () =>
    Promise.resolve(new Response(new Blob(["jpeg"]), { headers: { ETag: '"t"' } }));
  let reply: () => Promise<Response> = found;
  const fetched = vi.fn((_url: string, init: RequestInit) => {
    const signal = init.signal as AbortSignal;
    requests.push({ signal, tag: new Headers(init.headers).get("If-None-Match") });
    return reply();
  });
  const card = () => document.querySelector("canvas");
  const elapse = (ms: number) => act(async () => void vi.advanceTimersByTime(ms));

  beforeEach(() => {
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
    vi.stubGlobal("fetch", fetched);
    vi.stubGlobal("createImageBitmap", () => {
      const bitmap = { width: 560, height: 315, close: vi.fn() };
      bitmaps.push(bitmap);
      return Promise.resolve(bitmap);
    });
    vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue({
      drawImage: vi.fn(),
    } as unknown as CanvasRenderingContext2D);
  });
  afterEach(() => {
    vi.useRealTimers();
    fetched.mockClear();
    requests.length = 0;
    bitmaps.length = 0;
    reply = found;
  });

  it("shows a video's first frame once the pointer has rested, and drops it on leaving", async () => {
    const room = await renderTree();
    const row = screen.getByRole("treeitem", { name: /^v\.mp4/ });
    // A row with a picture has no text tip, which would show beside its card.
    expect(row.hasAttribute("title")).toBe(false);
    fireEvent.pointerEnter(row);
    expect(fetched.mock.calls[0][0]).toBe(`/api/thumbnail?session=${room.id}&entry=${TREE.ids.v}`);
    await act(async () => {});
    expect(card()).toBeNull();
    await elapse(80);
    expect(card()).not.toBeNull();
    fireEvent.pointerLeave(row);
    expect(card()).toBeNull();
    expect(requests[0].signal.aborted).toBe(true);
    expect(bitmaps[0].close).toHaveBeenCalled();
  });

  it("drops the card when its row goes away or its section folds under a still pointer", async () => {
    const room = await renderTree();
    const row = screen.getByRole("treeitem", { name: /^v\.mp4/ });
    fireEvent.pointerEnter(row);
    act(() => room.show(TREE, "v-removed"));
    expect(requests[0].signal.aborted).toBe(true);
    await elapse(80);
    expect(card()).toBeNull();
    act(() => room.show(TREE));
    fireEvent.pointerEnter(screen.getByRole("treeitem", { name: /^v\.mp4/ }));
    await elapse(80);
    expect(card()).not.toBeNull();
    act(() => room.show(TREE, "v-removed"));
    expect(card()).toBeNull();
    expect(requests[1].signal.aborted).toBe(true);
    expect(bitmaps[bitmaps.length - 1].close).toHaveBeenCalled();

    act(() => room.show(TREE));
    fireEvent.pointerEnter(screen.getByRole("treeitem", { name: /^v\.mp4/ }));
    await elapse(80);
    fireEvent.click(screen.getByRole("button", { name: /^Videos/ }));
    expect(card()).toBeNull();
    expect(requests[2].signal.aborted).toBe(true);
  });

  it("refreshes a resting card without overlapping requests, keeping the picture while it is unchanged", async () => {
    await renderTree();
    fireEvent.pointerEnter(screen.getByRole("treeitem", { name: /^v\.mp4/ }));
    await elapse(80);
    const [first] = bitmaps;

    reply = () => Promise.resolve(new Response(null, { status: 304 }));
    await elapse(1000);
    expect(requests[1].tag).toBe('"t"');
    expect(bitmaps).toHaveLength(1);
    expect(first.close).not.toHaveBeenCalled();
    expect(card()).not.toBeNull();

    reply = found;
    await elapse(1000);
    expect(bitmaps).toHaveLength(2);
    expect(first.close).toHaveBeenCalled();
    expect(bitmaps[1].close).not.toHaveBeenCalled();

    let finish: (response: Response) => void = () => undefined;
    reply = () => new Promise((resolve) => void (finish = resolve));
    await elapse(1000);
    await elapse(5000);
    expect(fetched).toHaveBeenCalledTimes(4);
    await act(async () => finish(new Response(null, { status: 422 })));
    expect(card()).toBeNull();
    expect(bitmaps[1].close).toHaveBeenCalled();

    reply = found;
    await elapse(1000);
    expect(requests[4].tag).toBeNull();
    expect(card()).not.toBeNull();
  });

  it("closes a picture that finishes decoding in the batch that unmounts the tree", async () => {
    await renderTree();
    let finish: (bitmap: unknown) => void = () => undefined;
    vi.stubGlobal("createImageBitmap", () => new Promise((resolve) => void (finish = resolve)));
    fireEvent.pointerEnter(screen.getByRole("treeitem", { name: /^v\.mp4/ }));
    await elapse(80);
    const bitmap = { width: 560, height: 315, close: vi.fn() };
    await act(async () => {
      finish(bitmap);
      await vi.advanceTimersByTimeAsync(0);
      cleanup();
    });
    expect(bitmap.close).toHaveBeenCalledTimes(1);
  });

  it("closes a picture that a newer one overtakes before it is drawn", async () => {
    await renderTree();
    const decoding: ((bitmap: unknown) => void)[] = [];
    vi.stubGlobal("createImageBitmap", () => new Promise((resolve) => void decoding.push(resolve)));
    fireEvent.pointerEnter(screen.getByRole("treeitem", { name: /^v\.mp4/ }));
    await elapse(80);
    const [older, newer] = [1, 2].map(() => ({ width: 560, height: 315, close: vi.fn() }));
    await act(async () => {
      decoding[0](older);
      await vi.advanceTimersByTimeAsync(1000);
      decoding[1](newer);
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(card()).not.toBeNull();
    expect(older.close).toHaveBeenCalledTimes(1);
    expect(newer.close).not.toHaveBeenCalled();
  });

  it("asks for no picture of a playlist or a lane, each entry's own for an entry, and a live stream's by its item", async () => {
    const runs = scenario("runs");
    const room = await renderTree(runs);
    const rows = screen.getAllByRole("treeitem");
    expect(labels()).toEqual(["runs / Train", "A", "GT", "runs / Train (2)", "A", "GT"]);
    for (const index of [0, 2, 3, 5]) {
      fireEvent.pointerEnter(rows[index]);
      fireEvent.pointerLeave(rows[index]);
    }
    expect(fetched).not.toHaveBeenCalled();

    for (const [index, id] of [
      [1, runs.ids["runs/Train/A"]],
      [4, runs.ids["runs/Train (2)/A"]],
    ] as const) {
      fireEvent.pointerEnter(rows[index]);
      fireEvent.pointerLeave(rows[index]);
      const url = new URLSearchParams({ session: room.id, entry: id });
      expect(fetched).toHaveBeenLastCalledWith(`/api/thumbnail?${url}`, expect.anything());
    }
    expect(fetched).toHaveBeenCalledTimes(2);

    cleanup();
    FakeSocket.reset();
    const live = await renderTree(KINDS);
    fireEvent.pointerEnter(screen.getByRole("treeitem", { name: /^Lobby/ }));
    const url = new URLSearchParams({ session: live.id, entry: KINDS.ids.cam });
    expect(fetched).toHaveBeenLastCalledWith(`/api/thumbnail?${url}`, expect.anything());
  });
});
