import { useEffect, useMemo, useRef, useState } from "react";
import type { Hidden } from "../viewer/hidden";
import { LaneChip } from "./LaneChip";
import type { PlaybackView, PlaybackSession } from "./session";
import { fitPicture } from "./renderer";
import { VideoCanvas, type LaneLayout } from "./VideoCanvas";

/** How many columns `count` lanes sit in: one row of one or two, else the smallest square that holds them. */
export function columnsFor(count: number): number {
  return count <= 2 ? count : Math.ceil(Math.sqrt(count));
}

/**
 * Where each of `lanes` (lane indices) goes in a grid filling `width` by `height` CSS pixels, left to right and top to
 * bottom. A last row with fewer lanes shares the width among them. Edges are whole pixels that neighbours share.
 */
export function layoutLanes(lanes: readonly number[], width: number, height: number): LaneLayout[] {
  const columns = columnsFor(lanes.length);
  if (columns === 0) return [];
  const rows = Math.ceil(lanes.length / columns);
  return lanes.map((lane, at) => {
    const row = Math.floor(at / columns);
    const inRow = Math.min(columns, lanes.length - row * columns);
    const column = at - row * columns;
    const x = Math.round((column * width) / inRow);
    const y = Math.round((row * height) / rows);
    return {
      lane,
      rect: {
        x,
        y,
        width: Math.round(((column + 1) * width) / inRow) - x,
        height: Math.round(((row + 1) * height) / rows) - y,
      },
    };
  });
}

/** The size, in CSS pixels, each lane's picture is displayed at in `cells`, by lane; hidden lanes get none. */
export function displayedSizes(
  cells: readonly LaneLayout[],
  lanes: number,
  picture: { width: number; height: number },
): { width: number; height: number }[] {
  const sizes = Array.from({ length: lanes }, () => ({ width: 0, height: 0 }));
  for (const { lane, rect } of cells) {
    if (rect.width < 1 || rect.height < 1) continue;
    const { width, height } = fitPicture(rect, picture);
    sizes[lane] = { width, height };
  }
  return sizes;
}

/** The size of an element's content box in CSS pixels, kept current. */
function useSize(): [React.RefObject<HTMLDivElement | null>, { width: number; height: number }] {
  const element = useRef<HTMLDivElement>(null);
  const [size, setSize] = useState({ width: 0, height: 0 });
  useEffect(() => {
    const box = element.current;
    if (!box) return;
    const observer = new ResizeObserver(([entry]) =>
      setSize({ width: entry.contentRect.width, height: entry.contentRect.height }),
    );
    observer.observe(box);
    return () => observer.disconnect();
  }, []);
  return [element, size];
}

/**
 * The picture in a grid of lane cells, each with its name tag. Lanes the user hid leave the grid and stop being sent;
 * the session is told the size each lane is displayed at so the backend draws overlays for it.
 */
export function LaneGrid({
  session,
  view,
  name,
  entry,
  hidden,
  onHide,
}: {
  session: PlaybackSession;
  view: PlaybackView;
  name: string;
  /** The entry the lanes belong to, as the workspace names it. */
  entry: string;
  hidden: Hidden;
  onHide: (label: string) => void;
}) {
  const [box, size] = useSize();
  const { lanes, width, height } = view;
  const shown = useMemo(
    () => lanes.flatMap((lane, index) => (hidden.lane(entry, lane.label) ? [] : [index])),
    [lanes, hidden, entry],
  );
  const cells = useMemo(() => layoutLanes(shown, size.width, size.height), [shown, size]);
  useEffect(() => {
    lanes.forEach((lane, index) => {
      const visible = shown.includes(index);
      if (lane.view.visible !== visible) session.setLaneView(index, { visible });
    });
  }, [session, lanes, shown]);
  useEffect(() => {
    if (width > 0 && height > 0)
      session.setLaneTargets(
        displayedSizes(cells, lanes.length, { width, height }),
        window.devicePixelRatio,
      );
  }, [session, cells, lanes.length, width, height]);
  return (
    <div ref={box} className="absolute inset-0 flex">
      <VideoCanvas session={session} label={name} cells={cells} />
      {cells.map(({ lane: index, rect }) => (
        <div
          key={lanes[index].label}
          className="absolute p-s"
          style={{ left: rect.x, top: rect.y, maxWidth: rect.width }}
        >
          <LaneChip
            lane={lanes[index]}
            onChange={(change) => session.setLaneView(index, change)}
            onHide={() => onHide(lanes[index].label)}
          />
        </div>
      ))}
      {shown.length === 0 && lanes.length > 0 && (
        <p role="status" className="m-auto text-on-canvas-muted">
          All lanes are hidden
        </p>
      )}
    </div>
  );
}
