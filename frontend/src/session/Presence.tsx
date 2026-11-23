import { useEffect, useLayoutEffect, useReducer, useRef, useState } from "react";
import { Icon } from "../kit/Icon";
import { cursorPointAt, placeCursor, type Scopes } from "./cursor";
import { useCursors, useParticipants, useSession } from "./session";

/** How many `--presence-N` colors tokens.css defines. */
const PRESENCE_COLORS = 8;

/** The CSS color of a participant, from the color number the backend gave them. */
export const presenceColor = (color: number) => `var(--presence-${color % PRESENCE_COLORS})`;

/** Tells the session where this pointer is, once per animation frame, and that it left the window. */
function useSendCursor(scopes: Scopes, active: boolean) {
  const { moveCursor } = useSession();
  const current = useRef(scopes);
  useEffect(() => {
    current.current = scopes;
  });
  useEffect(() => {
    if (!active) return;
    let frame = 0;
    let pending: PointerEvent | null = null;
    let shown = false;
    const send = (at: ReturnType<typeof cursorPointAt>) => {
      if (at || shown) moveCursor(at);
      shown = at !== null;
    };
    const flush = () => {
      frame = 0;
      if (pending)
        send(cursorPointAt(pending.target, pending.clientX, pending.clientY, current.current));
    };
    const onMove = (event: PointerEvent) => {
      pending = event;
      frame ||= requestAnimationFrame(flush);
    };
    // A pointer that leaves the window is "out" of an element with nothing to go "over".
    const onOut = (event: PointerEvent) => {
      if (event.relatedTarget) return;
      cancelAnimationFrame(frame);
      frame = 0;
      pending = null;
      send(null);
    };
    document.addEventListener("pointermove", onMove);
    document.addEventListener("pointerout", onOut);
    return () => {
      document.removeEventListener("pointermove", onMove);
      document.removeEventListener("pointerout", onOut);
      cancelAnimationFrame(frame);
      if (shown) moveCursor(null);
    };
  }, [active, moveCursor]);
}

type Spots = ReadonlyMap<number, { x: number; y: number }>;

/**
 * Shows the other participants' cursors over the window and sends this one's, as the two cursor options allow. A
 * cursor lands inside the element the sender pointed at, or by region where this window shows the same thing, and
 * is hidden where neither exists here. Positions are measured after layout, and again on scroll, resize and every
 * cursor move, because any of them can move the element. Nothing above this component re-renders when a cursor moves.
 */
export function Presence({ scopes }: { scopes: Scopes }) {
  const { cursorOptions } = useSession();
  const { participants } = useParticipants();
  const cursors = useCursors();
  const [, measureAgain] = useReducer((count: number) => count + 1, 0);
  const [spots, setSpots] = useState<Spots>(new Map());
  // Alone in the Session there is nobody to show a cursor to or to see one of.
  const inSession = participants.length > 1;
  useSendCursor(scopes, inSession && cursorOptions.share);

  useEffect(() => {
    let frame = 0;
    const later = () => {
      frame ||= requestAnimationFrame(() => {
        frame = 0;
        measureAgain();
      });
    };
    // Scroll does not bubble, so a captured listener sees every scroll container.
    document.addEventListener("scroll", later, true);
    window.addEventListener("resize", later);
    return () => {
      document.removeEventListener("scroll", later, true);
      window.removeEventListener("resize", later);
      cancelAnimationFrame(frame);
    };
  }, []);

  useLayoutEffect(() => {
    const next = new Map<number, { x: number; y: number }>();
    if (inSession && cursorOptions.show)
      for (const [id, point] of cursors) {
        const spot = placeCursor(point, scopes, document);
        if (spot) next.set(id, spot);
      }
    const same =
      next.size === spots.size &&
      [...next].every(([id, spot]) => spots.get(id)?.x === spot.x && spots.get(id)?.y === spot.y);
    if (!same) setSpots(next);
  });

  const people = new Map(participants.map((participant) => [participant.id, participant]));
  return (
    <div aria-hidden className="pointer-events-none fixed inset-0 z-50 overflow-hidden">
      {[...spots].map(([id, spot]) => {
        const participant = people.get(id);
        if (!participant) return null;
        const color = presenceColor(participant.color);
        return (
          <div
            key={id}
            className="absolute flex items-start"
            style={{ left: spot.x, top: spot.y, color }}
          >
            <Icon name="mouse-pointer-2" />
            <span
              className="mt-s rounded-control px-s text-small whitespace-nowrap"
              style={{ background: color, color: "var(--presence-fg)" }}
            >
              {participant.name}
            </span>
          </div>
        );
      })}
    </div>
  );
}
