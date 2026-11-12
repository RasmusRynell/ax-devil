import type { ContentRef, SessionCommand } from "../generated/api";

type Command = Extract<SessionCommand, { type: "requestTicket" | "releaseStreams" }>;
type Queued = {
  key: ContentRef;
  resolve: (ticket: string) => void;
  reject: (reason: Error) => void;
};

/** A seat's admission lane: releases share its next ticket request or one end-of-task command. */
export class StreamRequests {
  #queued: Queued[] = [];
  #releasing = new Set<number>();

  constructor(
    private readonly online: () => boolean,
    private readonly ask: (command: Command) => Promise<string | undefined>,
  ) {}

  /** Offline releases only cancel that viewer's queued request: a new lease sweeps its older streams. */
  release(viewer: number): void {
    if (!this.online()) {
      this.#queued = this.#queued.filter((queued) => {
        if (queued.key.viewer !== viewer) return true;
        queued.reject(new Error("The viewer closed."));
        return false;
      });
      return;
    }
    this.#releasing.add(viewer);
    if (this.#releasing.size === 1) queueMicrotask(() => this.#releaseStreams());
  }

  /** Sends synchronously when online, consuming every pending release before the next request. */
  request(key: ContentRef): Promise<string> {
    if (!this.online())
      return new Promise((resolve, reject) => this.#queued.push({ key, resolve, reject }));
    this.#releasing.delete(key.viewer);
    const release = [...this.#releasing];
    this.#releasing.clear();
    return this.ask({ type: "requestTicket", key, release }).then((ticket) => {
      if (ticket === undefined) throw new Error("The backend sent no ticket.");
      return ticket;
    });
  }

  #releaseStreams(): void {
    if (!this.online() || this.#releasing.size === 0) return;
    const viewers = [...this.#releasing];
    this.#releasing.clear();
    void this.ask({ type: "releaseStreams", viewers }).catch(() => undefined);
  }

  /** Sends queued requests in call order after welcome; releases were cancelled while offline. */
  flush(): void {
    if (this.online())
      for (const { key, resolve, reject } of this.#queued.splice(0))
        void this.request(key).then(resolve, reject);
  }

  /** Rejects requests whose connection went away and discards releases superseded by its next lease. */
  refuse(reason: Error): void {
    for (const { reject } of this.#queued.splice(0)) reject(reason);
    this.#releasing.clear();
  }
}
