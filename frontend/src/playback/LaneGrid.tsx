import { useAccess } from "../client/access";
import { useEffect, useMemo, useRef, useState } from "react";
import { choicesOf } from "../export/lanes";
import type { Hidden } from "../viewer/hidden";
import type { LaneDisplay, Picked, Selection, Viewport } from "../lanes/contract";
import { InfoHud } from "./InfoHud";
import { LaneChip } from "../lanes/LaneChip";
import { VideoLayer } from "../plugins/PluginFrame";
import { LaneViewsContext } from "../lanes/LaneMenu";
import { usePluginViews, viewName } from "../plugins/listing";
import type { BoundViewer } from "../viewer/bound";
import { allHidden, LANE_GAP, laneAt, layoutLanes, shownLanes } from "./laneCells";
import type { CellRect } from "../overlay/painter";
import type { Geometry } from "./renderer";
import type { PlaybackView } from "./view";
import { VideoCanvas } from "./VideoCanvas";
import { FIT, intersection, pictureRect } from "./viewport";

/** The visible part of the video's picture inside `cell`, which is the cell itself until the video has a size. */
function pictureIn(cell: CellRect, view: PlaybackView, viewport: Viewport): CellRect {
  return view.width > 0
    ? (intersection(
        pictureRect(cell, { width: view.width, height: view.height }, viewport),
        cell,
      ) ?? cell)
    : cell;
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
 * The picture in a grid of lane cells, each with its name tag (and the info HUD, when asked for). Lanes the user hid leave the grid and stop being sent.
 * With `only` the grid lays out that lane alone and the others keep being sent, so they play on out of sight.
 */
export function LaneGrid({
  stream,
  view,
  name,
  title,
  anchor,
  entry,
  bound,
  hidden,
  only = null,
  info = false,
  live = false,
  onHide,
  onExport,
  onPoint,
  onPick,
  viewport,
  onViewport,
  objectDetails,
  laneNames = true,
  onHover,
  onSelect,
  onDrawn,
}: {
  stream: LaneDisplay;
  view: PlaybackView;
  name: string;
  /** The viewer's title: a single lane named the same needs no name tag. */
  title?: string;
  /** Names the elements over the pictures for other windows' pointers; see `VideoCanvas`. */
  anchor: string;
  /** The entry the lanes belong to, as the workspace names it. */
  entry: string;
  /** The viewer the plugin views drive; absent where none do (a live viewer). */
  bound?: BoundViewer;
  hidden: Hidden;
  /** Shows this lane alone, filling the grid, while it is shown at all. */
  only?: number | null;
  /** Shows the info HUD over every lane's picture, in place of the lane's name. */
  info?: boolean;
  /** The lanes are live: their menus offer only what a live lane has, which is sticky. */
  live?: boolean;
  /** Hides a lane by its label; absent for a seat that may not change the workspace. */
  onHide?: (label: string) => void;
  /** Exports one lane, which each lane's menu then offers; absent where nothing is exported (a live viewer). */
  onExport?: (lane: number) => void;
  /** Told which lane the pointer is over (null: none) and which lane a press landed on. */
  onPoint?: (lane: number | null) => void;
  onPick?: (lane: number) => void;
  /** The zoom and pan of every lane's picture; see `VideoCanvas` for the gestures that ask to change it. */
  viewport?: Viewport;
  onViewport?: (viewport: Viewport) => void;
  /** Whether pointing at an object reports it (hover and click); on unless false. */
  objectDetails?: boolean;
  /** Whether each lane's chip shows its name; on unless false. */
  laneNames?: boolean;
  onHover?: (picked: Picked | null) => void;
  onSelect?: (selection: Selection) => void;
  /** Called after every draw with the pictures' geometry, to place outlines over the objects. */
  onDrawn?: (geometry: Geometry) => void;
}) {
  const [box, size] = useSize();
  const { lanes } = view;
  // The plugin views on over each lane, by `<lane label>|<view name>`.
  const videoViews = usePluginViews("video");
  const [layers, setLayers] = useState<ReadonlySet<string>>(new Set());
  const toggleLayer = (key: string) =>
    setLayers((now) => {
      const next = new Set(now);
      if (!next.delete(key)) next.add(key);
      return next;
    });
  const viewsOn = (label: string) =>
    videoViews.filter((plugin) => layers.has(`${label}|${viewName(plugin)}`));
  const { owner } = useAccess();
  const shown = useMemo(() => shownLanes(lanes, hidden, entry), [lanes, hidden, entry]);
  const cells = useMemo(
    () =>
      layoutLanes(
        only !== null && shown.includes(only) ? [only] : shown,
        size.width,
        size.height,
        LANE_GAP,
      ),
    [shown, only, size],
  );
  const laneOf = (event: { clientX: number; clientY: number }) => {
    const at = (box.current as HTMLDivElement).getBoundingClientRect();
    return laneAt(cells, event.clientX - at.left, event.clientY - at.top);
  };
  useEffect(() => {
    lanes.forEach((lane, index) => {
      const visible = shown.includes(index);
      if (lane.settings.visible !== visible) stream.setLane(index, { visible });
    });
  }, [stream, lanes, shown]);
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
        stream={stream}
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
      {bound &&
        cells.flatMap(({ lane: index, rect: cell }) =>
          viewsOn(lanes[index].label).map((plugin) => (
            <VideoLayer
              key={`${lanes[index].label}|${viewName(plugin)}`}
              view={plugin}
              target={bound}
              cell={cell}
              picture={pictureIn(cell, view, viewport ?? FIT)}
            />
          )),
        )}
      {cells.map(({ lane: index, rect: cell }) => {
        // The tag starts at the corner of the visible picture, not of the cell around it, and may run over the bars.
        const rect = pictureIn(cell, view, viewport ?? FIT);
        return (
          <div
            key={lanes[index].label}
            className="pointer-events-none absolute flex flex-col items-start gap-s p-inset"
            style={{
              left: rect.x,
              top: rect.y,
              maxWidth: cell.x + cell.width - rect.x,
              maxHeight: cell.y + cell.height - rect.y,
            }}
          >
            <div className="pointer-events-auto max-w-full">
              <LaneViewsContext
                value={
                  bound && videoViews.length > 0
                    ? {
                        views: videoViews,
                        shown: new Set(viewsOn(lanes[index].label).map(viewName)),
                        toggle: (name) => toggleLayer(`${lanes[index].label}|${name}`),
                      }
                    : null
                }
              >
                <LaneChip
                  lane={lanes[index]}
                  catalogs={view.catalogs}
                  cadence={view.cadence}
                  hideName={
                    !laneNames || info || (shown.length === 1 && lanes[index].label === title)
                  }
                  live={live}
                  stage={{ position: shown.indexOf(index) + 1, count: shown.length }}
                  onChange={(change) => stream.setLane(index, change)}
                  onApplyAll={
                    lanes.filter((lane) => lane.source !== null).length > 1
                      ? () => void stream.applyToAll(index, owner)
                      : undefined
                  }
                  onReload={() => stream.reloadCatalog(index)}
                  onDismissCatalogAction={() => stream.dismissCatalogAction(index)}
                  onHide={onHide && (() => onHide(lanes[index].label))}
                  onExport={
                    onExport && {
                      run: () => onExport(index),
                      waits: !!choicesOf(lanes).find((choice) => choice.lane === index)?.cold,
                    }
                  }
                />
              </LaneViewsContext>
            </div>
            {info && <InfoHud stream={stream} view={view} lane={index} />}
          </div>
        );
      })}
      {allHidden(lanes, shown) && (
        <p role="status" className="m-auto text-on-canvas-muted">
          All lanes are hidden
        </p>
      )}
    </div>
  );
}
