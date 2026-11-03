import { useEffect, useMemo, useRef, useState } from "react";
import type { Hidden } from "../viewer/hidden";
import type { Picked, Selection, Viewport } from "./contract";
import { LaneChip } from "./LaneChip";
import { laneAt, layoutLanes, shownLanes } from "./laneCells";
import type { Geometry } from "./renderer";
import type { PlaybackSession } from "./session";
import type { PlaybackView } from "./view";
import { VideoCanvas } from "./VideoCanvas";
import { FIT, intersection, pictureRect } from "./viewport";

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
 * With `only` the grid lays out that lane alone and the others keep being sent, so they play on out of sight.
 */
export function LaneGrid({
  session,
  view,
  name,
  anchor,
  entry,
  hidden,
  only = null,
  onHide,
  onPoint,
  onPick,
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
  /** Shows this lane alone, filling the grid, while it is shown at all. */
  only?: number | null;
  /** Hides a lane by its label; absent for a seat that may not change the workspace. */
  onHide?: (label: string) => void;
  /** Told which lane the pointer is over (null: none) and which lane a press landed on. */
  onPoint?: (lane: number | null) => void;
  onPick?: (lane: number) => void;
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
  const shown = useMemo(() => shownLanes(lanes, hidden, entry), [lanes, hidden, entry]);
  const cells = useMemo(
    () =>
      layoutLanes(only !== null && shown.includes(only) ? [only] : shown, size.width, size.height),
    [shown, only, size],
  );
  const laneOf = (event: { clientX: number; clientY: number }) => {
    const at = (box.current as HTMLDivElement).getBoundingClientRect();
    return laneAt(cells, event.clientX - at.left, event.clientY - at.top);
  };
  useEffect(() => {
    lanes.forEach((lane, index) => {
      const visible = shown.includes(index);
      if (lane.settings.visible !== visible) session.setLane(index, { visible });
    });
  }, [session, lanes, shown]);
  return (
    <div
      ref={box}
      className="absolute inset-0 flex"
      onPointerMove={(event) => onPoint?.(laneOf(event))}
      onPointerLeave={() => onPoint?.(null)}
      onPointerDown={(event) => {
        const lane = laneOf(event);
        if (lane !== null) onPick?.(lane);
      }}
    >
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
      {cells.map(({ lane: index, rect: cell }) => {
        // The tag starts at the corner of the visible picture, not of the cell around it, and may run over the bars.
        const rect =
          view.width > 0
            ? (intersection(
                pictureRect(cell, { width: view.width, height: view.height }, viewport ?? FIT),
                cell,
              ) ?? cell)
            : cell;
        return (
          <div
            key={lanes[index].label}
            className="pointer-events-none absolute p-s *:pointer-events-auto"
            style={{ left: rect.x, top: rect.y, maxWidth: cell.x + cell.width - rect.x }}
          >
            <LaneChip
              lane={lanes[index]}
              catalogs={view.catalogs}
              cadence={view.cadence}
              onChange={(change) => session.setLane(index, change)}
              onApplyAll={
                lanes.filter((lane) => lane.source !== null).length > 1
                  ? () => session.applyToAll(index)
                  : undefined
              }
              onHide={onHide && (() => onHide(lanes[index].label))}
            />
          </div>
        );
      })}
      {shown.length === 0 && lanes.length > 0 && (
        <p role="status" className="m-auto text-on-canvas-muted">
          All lanes are hidden
        </p>
      )}
    </div>
  );
}
