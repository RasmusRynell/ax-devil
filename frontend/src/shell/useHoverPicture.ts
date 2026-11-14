import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { apiFetch } from "../client/api";

/** How long the pointer must rest before the card shows, so sweeping across items shows none. */
const INTENT_MS = 80;
/** The wait between the end of one request and the start of the next while the pointer rests. */
const REFRESH_MS = 1000;

/** Where the pointer entered, and the picture URL the hover is on. */
type Hover = { url: string; x: number; y: number };

/** The card to draw: a picture and the pointer position it opens at. */
export type Shown = { bitmap: ImageBitmap; x: number; y: number };

/**
 * The picture of whatever the pointer rests on, from a URL, for a card by the pointer. `urls` holds the pictures
 * currently on offer: a hover ends for good once its URL is not among them, since a removed item sends no pointer
 * event. `enter` starts fetching at once and shows the card once the pointer has rested for `INTENT_MS` and the
 * picture has arrived. The card stays where the pointer entered and refreshes every `REFRESH_MS` after the last
 * request ended, so requests never overlap; a 304 to the held picture's ETag keeps it without decoding anything.
 * A failed request hides the card and the next refresh may bring it back. `leave`, and any scroll, wheel, press,
 * key press or window blur, ends the hover and cancels the request.
 */
export function useHoverPicture(urls: ReadonlySet<string>) {
  const [hover, setHover] = useState<Hover | null>(null);
  const active = hover && urls.has(hover.url) ? hover : null;
  const [held, setHeld] = useState<{ hover: Hover; bitmap: ImageBitmap } | null>(null);

  const enter = useCallback(
    (url: string | null, x: number, y: number) => setHover(url ? { url, x, y } : null),
    [],
  );
  const leave = useCallback(() => setHover(null), []);
  // A hover that ended must not come back with its item.
  useEffect(() => {
    if (hover && !active) setHover(null);
  }, [hover, active]);
  // A bitmap handed to `setHeld` stays ours until it commits. If a later update, the end of the hover or an unmount
  // wins first, it never commits and is closed here.
  const queued = useRef<ImageBitmap | null>(null);
  useLayoutEffect(() => {
    if (held && queued.current === held.bitmap) queued.current = null;
  }, [held]);
  // A replaced bitmap closes only once its replacement is committed.
  useEffect(() => () => held?.bitmap.close(), [held]);

  useEffect(() => {
    if (!active) return;
    const abort = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;
    let rested = false;
    let etag: string | null = null;
    let arrived: ImageBitmap | null = null; // Decoded, not yet shown.
    const dropQueued = () => {
      queued.current?.close();
      queued.current = null;
    };
    const publish = () => {
      if (!rested || !arrived) return;
      dropQueued();
      queued.current = arrived;
      setHeld({ hover: active, bitmap: arrived });
      arrived = null;
    };

    const request = async () => {
      try {
        const response = await apiFetch(active.url, {
          signal: abort.signal,
          headers: etag ? { "If-None-Match": etag } : undefined,
        });
        if (response.status === 304) return;
        if (!response.ok) throw new Error(`Picture request failed: ${response.status}`);
        const bitmap = await createImageBitmap(await response.blob());
        if (abort.signal.aborted) bitmap.close();
        else {
          etag = response.headers.get("ETag");
          arrived?.close();
          arrived = bitmap;
          publish();
        }
      } catch {
        if (abort.signal.aborted) return;
        etag = null; // Aborted or failed: no card, no message.
        arrived?.close();
        arrived = null;
        dropQueued();
        setHeld(null);
      } finally {
        if (!abort.signal.aborted) timer = setTimeout(() => void request(), REFRESH_MS);
      }
    };
    void request();
    const intent = setTimeout(() => {
      rested = true;
      publish();
    }, INTENT_MS);

    const hide = () => setHover(null);
    const gestures = ["pointerdown", "keydown", "wheel", "scroll"] as const;
    for (const gesture of gestures) document.addEventListener(gesture, hide, true);
    window.addEventListener("blur", hide);
    return () => {
      abort.abort();
      clearTimeout(timer);
      clearTimeout(intent);
      arrived?.close();
      dropQueued();
      setHeld(null);
      for (const gesture of gestures) document.removeEventListener(gesture, hide, true);
      window.removeEventListener("blur", hide);
    };
  }, [active]);

  const shown: Shown | null =
    active && held?.hover === active ? { bitmap: held.bitmap, x: active.x, y: active.y } : null;
  return { shown, enter, leave };
}
