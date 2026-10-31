/**
 * The workspace as the backend reports it, and which viewers are open on it. The backend owns the workspace and
 * streams its whole state; this store only projects it and remembers which items already opened a viewer.
 */

import type { ServerEvent, WorkspaceState } from "../generated/api";

export type Connection = "connecting" | "open" | "lost";

export type WorkspaceView = {
  connection: Connection;
  /** `null` until the backend has sent the first state. */
  state: WorkspaceState | null;
  /** Items with a viewer open, in the order they opened. */
  viewers: readonly string[];
  /** The item whose viewer is shown. */
  active: string | null;
};

/** The part of `EventSource` the store uses. */
export interface EventSourceLike {
  onopen: ((event: Event) => void) | null;
  onmessage: ((event: MessageEvent<string>) => void) | null;
  onerror: ((event: Event) => void) | null;
  /** 2 once the browser has given up reconnecting by itself. */
  readonly readyState: number;
  close(): void;
}

export interface WorkspaceDeps {
  open: () => EventSourceLike;
  /** Calls back once after `ms`; returns the way to cancel. */
  after: (callback: () => void, ms: number) => () => void;
}

const RETRY_MS = [500, 1000, 2000, 4000, 8000];
const CLOSED = 2;

export class WorkspaceStore {
  readonly #deps: WorkspaceDeps;
  readonly #listeners = new Set<() => void>();
  /** Every item that has had a viewer, so the flag the backend never clears opens one only once. */
  readonly #opened = new Set<string>();
  /** What each open viewer last showed, by item id; a viewer that is shown again opens where it left off. */
  readonly #remembered = new Map<string, unknown>();
  #view: WorkspaceView = { connection: "connecting", state: null, viewers: [], active: null };
  #source: EventSourceLike | undefined;
  #cancelRetry: (() => void) | undefined;
  #retries = 0;

  constructor(deps: WorkspaceDeps) {
    this.#deps = deps;
  }

  /** Starts following the backend's events. */
  start(): void {
    const source = this.#deps.open();
    this.#source = source;
    source.onopen = () => {
      this.#retries = 0;
      this.#set({ connection: "open" });
    };
    source.onmessage = ({ data }) => {
      const event = JSON.parse(data) as ServerEvent;
      if (event.type === "Workspace") this.#receive(event);
    };
    source.onerror = () => {
      this.#set({ connection: "lost" });
      // The browser retries a dropped connection itself; after a refusal it closes the source and never does.
      if (source.readyState !== CLOSED) return;
      this.#cancelRetry = this.#deps.after(
        () => this.start(),
        RETRY_MS[Math.min(this.#retries++, RETRY_MS.length - 1)],
      );
    };
  }

  stop(): void {
    this.#cancelRetry?.();
    this.#source?.close();
  }

  subscribe = (listener: () => void): (() => void) => {
    this.#listeners.add(listener);
    return () => this.#listeners.delete(listener);
  };

  getSnapshot = (): WorkspaceView => this.#view;

  /** What the item's viewer saved when it last went away, if it did. */
  remembered(itemId: string): unknown {
    return this.#remembered.get(itemId);
  }

  /** Keeps what an item's viewer shows, for when it is shown again. Closing the viewer forgets it. */
  remember(itemId: string, state: unknown): void {
    if (this.#view.viewers.includes(itemId)) this.#remembered.set(itemId, state);
  }

  /** Shows an open viewer. */
  activate(itemId: string): void {
    if (this.#view.viewers.includes(itemId)) this.#set({ active: itemId });
  }

  /** Closes a viewer. The item never opens another by itself. */
  close(itemId: string): void {
    const viewers = this.#view.viewers.filter((id) => id !== itemId);
    this.#remembered.delete(itemId);
    this.#set({
      viewers,
      active: this.#view.active === itemId ? (viewers.at(-1) ?? null) : this.#view.active,
    });
  }

  #receive({ name, items }: ServerEvent): void {
    const state: WorkspaceState = { name, items };
    const ids = new Set(items.map((item) => item.id));
    let viewers = this.#view.viewers.filter((id) => ids.has(id));
    for (const id of this.#remembered.keys()) if (!ids.has(id)) this.#remembered.delete(id);
    let active = this.#view.active;
    // The newest opened viewer is shown. A viewer opens when its item first appears with the flag; its content
    // mounts once the item has resolved, and until then the host shows the item as pending.
    for (const item of items) {
      if (!item.open || this.#opened.has(item.id)) continue;
      this.#opened.add(item.id);
      viewers = [...viewers, item.id];
      active = item.id;
    }
    if (active !== null && !viewers.includes(active)) active = viewers.at(-1) ?? null;
    this.#set({ connection: "open", state, viewers, active });
  }

  #set(change: Partial<WorkspaceView>): void {
    this.#view = { ...this.#view, ...change };
    this.#listeners.forEach((listener) => listener());
  }
}
