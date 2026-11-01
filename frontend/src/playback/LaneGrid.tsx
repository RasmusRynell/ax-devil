import { useEffect, useMemo, useRef, useState } from "react";
import type { Hidden } from "../viewer/hidden";
import type { Picked, Selection, Viewport } from "./contract";
import { LaneChip } from "./LaneChip";
import type { Geometry } from "./renderer";
import type { PlaybackSession } from "./session";
import type { PlaybackView } from "./view";
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
 * The picture in a grid of lane cells, each with its name tag. Lanes the user hid leave the grid and stop being sent.
 */
export function LaneGrid({
  session,
  view,
  name,
  anchor,
  entry,
  hidden,
  onHide,
  viewport,
  onViewport,
  objectDetails,
  onHover,
  onSelect,
  onDrawn,
}: {
  session: PlaybackSession;
  view: PlaybackView;
  name: string;
  /** Names the elements over the pictures for other windows' pointers; see `VideoCanvas`. */
  anchor: string;
  /** The entry the lanes belong to, as the workspace names it. */
  entry: string;
  hidden: Hidden;
  /** Hides a lane by its label; absent for a seat that may not change the workspace. */
  onHide?: (label: string) => void;
  /** The zoom and pan of every lane's picture; see `VideoCanvas` for the gestures that ask to change it. */
  viewport?: Viewport;
  onViewport?: (viewport: Viewport) => void;
  /** Whether pointing at an object reports it (hover and click); on unless false. */
  objectDetails?: boolean;
  onHover?: (picked: Picked | null) => void;
  onSelect?: (selection: Selection) => void;
  /** Called after every draw with the pictures' geometry, to place outlines over the objects. */
  onDrawn?: (geometry: Geometry) => void;
}) {
  const [box, size] = useSize();
  const { lanes } = view;
  const shown = useMemo(
    () => lanes.flatMap((lane, index) => (hidden.lane(entry, lane.label) ? [] : [index])),
    [lanes, hidden, entry],
  );
  const cells = useMemo(() => layoutLanes(shown, size.width, size.height), [shown, size]);
  useEffect(() => {
    lanes.forEach((lane, index) => {
      const visible = shown.includes(index);
      if (lane.settings.visible !== visible) session.setLane(index, { visible });
    });
  }, [session, lanes, shown]);
  return (
    <div ref={box} className="absolute inset-0 flex">
      <VideoCanvas
        session={session}
        label={name}
        anchor={anchor}
        cells={cells}
        viewport={viewport}
        onViewport={onViewport}
        objectDetails={objectDetails}
        onHover={onHover}
        onSelect={onSelect}
        onDrawn={onDrawn}
      />
      {cells.map(({ lane: index, rect }) => (
        <div
          key={lanes[index].label}
          className="pointer-events-none absolute p-s *:pointer-events-auto"
          style={{ left: rect.x, top: rect.y, maxWidth: rect.width }}
        >
          <LaneChip
            lane={lanes[index]}
            catalogs={view.catalogs}
            onChange={(change) => session.setLane(index, change)}
            onApplyAll={
              lanes.filter((lane) => lane.source !== null).length > 1
                ? () => session.applyToAll(index)
                : undefined
            }
            onHide={onHide && (() => onHide(lanes[index].label))}
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
