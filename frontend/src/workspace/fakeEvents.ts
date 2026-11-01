// A backend's event stream for tests: the test says what the backend would send.
import type { ItemState, ResolutionState } from "../generated/api";
import { WorkspaceStore, type EventSourceLike } from "./store";

export const pending: ResolutionState = { status: "Pending" };

export const resolved = (id: string): ResolutionState => ({
  status: "Resolved",
  content: {
    kind: "SeekableVideo",
    id: `content-${id}`,
    display_name: `${id}.mp4`,
    entry: { id: `content-${id}`, label: `${id}.mp4`, lanes: [`${id}.mp4`] },
  },
});

export const video = (id: string, open: boolean, resolution: ResolutionState): ItemState => ({
  id,
  label: `${id}.mp4`,
  open,
  resolution,
});

export type FakeSource = EventSourceLike & {
  fail(readyState: number): void;
  send(items: ItemState[]): void;
};

/** A store on a fake event source, to start itself or through a provider; every retry it schedules waits for the test. */
export function fakeWorkspace() {
  const sources: FakeSource[] = [];
  const retries: (() => void)[] = [];
  const store = new WorkspaceStore({
    open: () => {
      let state = 1;
      const source: FakeSource = {
        onopen: null,
        onmessage: null,
        onerror: null,
        get readyState() {
          return state;
        },
        close: () => {},
        fail(readyState) {
          state = readyState;
          this.onerror?.(new Event("error"));
        },
        send(items) {
          const data = JSON.stringify({ type: "Workspace", name: null, items, rows: [] });
          this.onmessage?.(new MessageEvent("message", { data }));
        },
      };
      sources.push(source);
      return source;
    },
    after: (callback) => {
      retries.push(callback);
      return () => {};
    },
  });
  return { store, sources, retries, source: () => sources[sources.length - 1] };
}
