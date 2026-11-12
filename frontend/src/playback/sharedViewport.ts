import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { Viewport as Region } from "../generated/api";
import {
  useCan,
  useCommand,
  useFollowing,
  useOwnerSeat,
  usePresenterChange,
  useTakesPresenter,
  useView,
} from "../session/session";
import type { Viewport } from "../lanes/contract";
import type { Geometry } from "./renderer";
import { FIT, regionOf, viewportOf } from "./viewport";

/** The least time between two viewport changes sent while a gesture goes on. */
export const REGION_EVERY_MS = 100;

/**
 * Sends the region a viewer shows, at most one every `REGION_EVERY_MS`. A region offered while another waits replaces
 * it, so the last one of a gesture is always sent. `busy` holds from the offer until the backend has answered.
 */
export class RegionSender {
  readonly #send: (region: Region | undefined) => Promise<void>;
  readonly #now: () => number;
  readonly #settled: () => void;
  #waiting: { region: Region | undefined } | null = null;
  #unanswered = 0;
  #sentAt = -REGION_EVERY_MS;
  #timer: ReturnType<typeof setTimeout> | undefined;

  constructor(
    send: (region: Region | undefined) => Promise<void>,
    settled: () => void,
    now = () => performance.now(),
  ) {
    this.#send = send;
    this.#settled = settled;
    this.#now = now;
  }

  /** Whether a region of this window is on its way and the backend has not settled it. */
  get busy(): boolean {
    return this.#waiting !== null || this.#unanswered > 0;
  }

  offer(region: Region | undefined): void {
    this.#waiting = { region };
    if (this.#timer !== undefined) return;
    const wait = this.#sentAt + REGION_EVERY_MS - this.#now();
    if (wait <= 0) this.flush();
    else this.#timer = setTimeout(() => this.flush(), wait);
  }

  /** Sends the region that waits, now. */
  flush(): void {
    clearTimeout(this.#timer);
    this.#timer = undefined;
    const waiting = this.#waiting;
    if (!waiting) return;
    this.#waiting = null;
    this.#sentAt = this.#now();
    this.#unanswered += 1;
    this.#send(waiting.region).finally(() => {
      this.#unanswered -= 1;
      if (!this.busy) this.#settled();
    });
  }
}

const same = (a: Viewport, b: Viewport) =>
  a.zoom === b.zoom && a.panX === b.panX && a.panY === b.panY;

/**
 * The zoom and pan of one viewer's pictures, shared through the Session's view as the region of the video they show.
 * The window's own zoom and pan show at once and are sent while they change. Another seat's region is taken when it
 * makes a change, and once when the viewer opens; a window that follows takes the presenter's whenever it changes, and
 * never one while a change of its own is on its way. A window that may not change its view cannot zoom: `onViewport`
 * is absent. `onDrawn` gives the hook the geometry that converts between a viewport and a region.
 */
export function useSharedViewport(viewer: number) {
  const [viewport, setViewport] = useState<Viewport>(FIT);
  const [viewers] = useView("viewers");
  const region = viewers[viewer]?.viewport;
  const canChange = useCan("view");
  const following = useFollowing();
  const ownerSeat = useOwnerSeat();
  const presenter = usePresenterChange();
  const takesPresenter = useTakesPresenter();
  const command = useCommand();
  const geometry = useRef<Geometry | null>(null);
  // The region to show once there is geometry to convert it with.
  const waiting = useRef(false);
  const latest = useRef(region);
  latest.current = region;
  const adopt = useCallback(() => {
    const at = geometry.current?.reference();
    const next = at && viewportOf(latest.current, at.cell, at.image);
    waiting.current = !next;
    if (next) setViewport((shown) => (same(shown, next) ? shown : next));
  }, []);
  // Another seat's region that arrived while a change of this window was unanswered, taken once it is answered.
  const skipped = useRef(false);
  const byOther = useRef(presenter.byOther);
  byOther.current = presenter.byOther;
  const sender = useMemo(
    () =>
      new RegionSender(
        (sent) =>
          // A change the backend refuses is taken back to the region it has.
          command({ type: "setViewport", viewer, viewport: sent }).catch(adopt),
        () => {
          if (skipped.current && byOther.current) adopt();
          skipped.current = false;
        },
      ),
    [command, viewer, adopt],
  );
  useEffect(() => () => sender.flush(), [sender]);

  const opened = useRef(false);
  useEffect(() => {
    const first = !opened.current;
    opened.current = true;
    const takes = ownerSeat && takesPresenter(viewer, presenter.byOther, "viewport");
    if (!(first || takes || (following && !ownerSeat))) return;
    if (sender.busy) skipped.current = true;
    else adopt();
  }, [
    region,
    ownerSeat,
    following,
    presenter.rev,
    presenter.byOther,
    viewer,
    takesPresenter,
    sender,
    adopt,
  ]);

  const onViewport = useCallback(
    (next: Viewport) => {
      setViewport(next);
      const at = geometry.current?.reference();
      sender.offer(at && regionOf(next, at.cell, at.image));
    },
    [sender],
  );
  const onDrawn = useCallback(
    (next: Geometry) => {
      geometry.current = next;
      if (waiting.current) adopt();
    },
    [adopt],
  );
  return { viewport, onViewport: canChange ? onViewport : undefined, onDrawn };
}
