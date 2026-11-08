import { useLayoutEffect, useRef, useState } from "react";
import { fontOf, measureText, type Font } from "./table/measure";

/** The fewest characters an id keeps when it is shortened; below that, ids are dropped instead. */
const MIN_ID_CHARS = 9;

/** `text` cut in the middle with `…` to fit `room`, as wide as `width` says. */
function elideMiddle(text: string, room: number, width: (text: string) => number): string {
  if (width(text) <= room) return text;
  for (let kept = text.length - 1; kept > 0; kept--) {
    const head = Math.ceil(kept / 2);
    const shortened = `${text.slice(0, head)}…${text.slice(text.length - (kept - head))}`;
    if (width(shortened) <= room) return shortened;
  }
  return "…";
}

/**
 * The ids joined by commas as they fit `room`: all of them whole, else each shortened in the middle, else the first
 * ones, with `+N` for how many are left out. `width` measures a text.
 */
export function relatedText(
  ids: readonly string[],
  room: number,
  width: (text: string) => number,
): string {
  const separator = width(", ");
  const smallest = MIN_ID_CHARS * width("0");
  for (let count = ids.length; count > 0; count--) {
    const kept = ids.slice(0, count);
    const more = count < ids.length ? `, +${ids.length - count}` : "";
    const space = room - width(more) - separator * (count - 1);
    const each = Math.floor(space / count);
    let shown: string[];
    if (kept.reduce((sum, id) => sum + width(id), 0) <= space) shown = kept;
    else if (count === 1 || each >= smallest)
      shown = kept.map((id) => elideMiddle(id, each, width));
    else continue;
    shown[shown.length - 1] += more;
    return shown.join(", ");
  }
  return "";
}

/** The ids an event involves, muted, shortened to the room the cell has; the whole list is the tooltip. */
export function RelatedIds({ ids }: { ids: readonly string[] }) {
  const box = useRef<HTMLDivElement>(null);
  const [room, setRoom] = useState<{ width: number; font: Font } | null>(null);
  useLayoutEffect(() => {
    const element = box.current;
    if (!element) return;
    const read = () => setRoom({ width: element.clientWidth, font: fontOf(element) });
    read();
    if (typeof ResizeObserver === "undefined") return;
    const watch = new ResizeObserver(read);
    watch.observe(element);
    return () => watch.disconnect();
  }, []);
  const full = ids.join(", ");
  // Until the cell has a width the ids show whole.
  const text =
    room === null || room.width === 0
      ? full
      : relatedText(ids, room.width, (text) => measureText(room.font, text));
  return (
    <div
      ref={box}
      title={full}
      className="overflow-hidden font-mono text-aside whitespace-pre text-muted"
    >
      {text}
    </div>
  );
}
