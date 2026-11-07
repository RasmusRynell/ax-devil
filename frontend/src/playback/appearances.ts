/**
 * Where one object appears on a lane's video, as the backend answers it for the object a lane's page is about. Asked
 * when the page opens and again when the answer would change (the lane's revisions, the file's
 * ingest ending, the viewer owning its frames again); answers of an older request, or made under other revisions, are
 * dropped.
 */

import type { EventRowWire } from "../generated/api";
import type { LaneSet } from "./lanes";
import { basisOf, FOLLOWING_RETRIES, FOLLOWING_RETRY_MS, readyOf } from "./fileBasis";
import type { ClientMessage, ServerMessage } from "./protocol";

/** The frames an object is shown on, as inclusive `[first, last]` spans, ascending, with a frame between two. */
export type Spans = readonly (readonly [number, number])[];

/** Where the object appears; replaced, never mutated, when it changes. */
export type Appearance = {
  entity: string;
  spans: Spans;
  /** More spans exist than were sent: these are the first ones. */
  truncated: boolean;
  /** The events that involve the object, in frame order. */
  events: readonly EventRowWire[];
  /** More events involve the object than were sent: these are the first ones. */
  eventsTruncated: boolean;
};

type Slot = {
  entity: string;
  view: Appearance | null;
  /** The request the answer awaited belongs to; `null` when none is awaited. */
  asked: number | null;
  /** What the last request was made under, as JSON; `null` when none stands. */
  basis: string | null;
  retries: number;
  cancel: (() => void) | null;
};

export class Appearances {
  readonly #lanes: LaneSet;
  readonly #send: (message: ClientMessage) => void;
  readonly #after: (callback: () => void, ms: number) => () => void;
  readonly #owner: () => boolean;
  readonly #changed: () => void;
  readonly #released: (lane: number) => void;
  readonly #slots = new Map<number, Slot>();
  #requests = 0;

  /**
   * `owner` says whether the viewer chooses its own frames; `changed` is called when a lane's view changes; `released`
   * when a lane that was asked about no longer has an object wanted, so that what the backend kept for it can be freed.
   */
  constructor(
    lanes: LaneSet,
    send: (message: ClientMessage) => void,
    after: (callback: () => void, ms: number) => () => void,
    owner: () => boolean,
    changed: () => void,
    released: (lane: number) => void,
  ) {
    this.#lanes = lanes;
    this.#send = send;
    this.#after = after;
    this.#owner = owner;
    this.#changed = changed;
    this.#released = released;
  }

  /** Where the object the lane wants appears; `null` until known, and while the backend cannot say. */
  view(lane: number): Appearance | null {
    return this.#slots.get(lane)?.view ?? null;
  }

  /** Whether an object's appearances are wanted on the lane. */
  wants(lane: number): boolean {
    return this.#slots.has(lane);
  }

  /** Wants to know where `entity` appears on the lane, or nothing with `null`, as when its page closes. */
  want(lane: number, entity: string | null): void {
    const held = this.#slots.get(lane);
    if (held?.entity === entity) return;
    held?.cancel?.();
    if (entity === null) {
      this.#slots.delete(lane);
      if (held && held.basis !== null) this.#released(lane);
    } else {
      const slot: Slot = { entity, view: null, asked: null, basis: null, retries: 0, cancel: null };
      this.#slots.set(lane, slot);
      this.#ask(lane, slot);
    }
    if (held?.view) this.#changed();
  }

  /** The lanes changed: ask again for each object whose answer would differ. */
  refresh(): void {
    for (const [lane, slot] of this.#slots) {
      if (slot.cancel !== null || this.#key(lane) === slot.basis) continue;
      slot.retries = 0;
      this.#ask(lane, slot);
    }
  }

  /** The socket is gone: forget what was awaited. The objects wanted stay wanted. */
  suspend(): void {
    for (const slot of this.#slots.values()) {
      slot.cancel?.();
      slot.cancel = null;
      slot.asked = null;
      slot.basis = null;
    }
  }

  /** Takes the backend's answer to a request, unless a newer request or other revisions have made it stale. */
  receive(message: Extract<ServerMessage, { type: "Appearances" }>): void {
    const slot = this.#slots.get(message.lane);
    if (!slot || slot.asked !== message.request || slot.entity !== message.entity) return;
    const answer = message.appearances;
    if (answer.state === "Found") {
      const state = this.#lanes.states[message.lane];
      if (
        readyOf(state)?.data_revision !== answer.data_revision ||
        state.policyRevision !== answer.policy_revision
      )
        return;
      slot.asked = null;
      this.#set(slot, {
        entity: slot.entity,
        spans: answer.spans,
        truncated: answer.truncated,
        events: answer.events,
        eventsTruncated: answer.events_truncated,
      });
      return;
    }
    slot.asked = null;
    // The backend may not yet know the viewer owns its frames again; nothing tells this client when it does.
    if (
      answer.unavailable.state === "Following" &&
      this.#owner() &&
      slot.retries < FOLLOWING_RETRIES
    ) {
      slot.retries++;
      slot.cancel = this.#after(() => {
        slot.cancel = null;
        if (this.#slots.get(message.lane) === slot) this.#ask(message.lane, slot);
      }, FOLLOWING_RETRY_MS);
    }
    this.#set(slot, null);
  }

  #key(lane: number): string {
    return JSON.stringify(basisOf(this.#lanes, lane, this.#owner()));
  }

  #ask(lane: number, slot: Slot): void {
    slot.basis = this.#key(lane);
    slot.asked = ++this.#requests;
    this.#send({
      type: "Appearances",
      lane,
      request: slot.asked,
      entity: slot.entity,
    });
    this.#set(slot, null);
  }

  #set(slot: Slot, view: Appearance | null): void {
    if (slot.view === view) return;
    slot.view = view;
    this.#changed();
  }
}
