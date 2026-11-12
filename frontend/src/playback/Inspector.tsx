import { Button } from "../kit/Button";
import { Chip } from "../kit/Chips";
import type { EventRowWire } from "../generated/api";
import { fixed } from "../present/expr/fixed";
import { categoryDot } from "../tools/category";
import { clockOf } from "../tools/eventItem";
import { ObjectEvents } from "../tools/ObjectEvents";
import { UNKNOWN_CLASS } from "../tools/objects";
import { PresenceStrip } from "../tools/PresenceStrip";
import { jumpsFrom, type Jumps } from "../inspect/appearanceJumps";
import type { Appearance } from "../inspect/appearances";
import type { InspectionView, Summary } from "../lanes/contract";
import type { InspectorMemory } from "../inspect/fields";
import { InspectorLines } from "../inspect/InspectorLines";
import type { FieldWire } from "../stream/protocol";
import type { PlaybackView } from "./view";
import { useAppearance, type AppearanceSource } from "../inspect/useAppearance";

const ATTRIBUTES_NOTE = "Attributes are only available while an object is in the frame.";

const NO_FIELDS: readonly FieldWire[] = [];

const decimals = (value: number): string => String(Number(value.toFixed(2)));

/** `"score S · confidence C"`, leaving out what is missing. */
function detailOf({ score, confidence }: Summary): string {
  const parts: string[] = [];
  if (score !== null) parts.push(`score ${decimals(score)}`);
  if (confidence !== null) parts.push(`confidence ${decimals(confidence)}`);
  return parts.join(" · ");
}

/** What an object page reads of the viewer to say where its object appears and to go there. */
export type Reach = {
  source: AppearanceSource;
  /** The frame on screen. */
  frame: number;
  /** How many frames the video has. */
  frames: number;
  /** The time of a frame in microseconds; `undefined` until the video is described. */
  timeOf: (frame: number) => number | undefined;
  /** Goes to a frame; absent for a seat that may not move the viewer. */
  seek?: (frame: number) => void;
};

/** What `source` and `view` let an object page see and do; `canSeek` is whether this seat may move the viewer. */
export const reachOf = (
  source: {
    files: AppearanceSource;
    frameTime(frame: number): number | undefined;
    seek(frame: number): void;
  },
  view: PlaybackView,
  canSeek: boolean,
): Reach => ({
  source: source.files,
  frame: view.shown?.sequenceId ?? view.playhead,
  frames: view.frameCount,
  timeOf: (frame) => source.frameTime(frame),
  seek: canSeek ? (frame) => source.seek(frame) : undefined,
});

/** When the object is next or last seen, from the frame on screen; empty when the spans cannot say. */
function whenOf(appearance: Appearance | null, jumps: Jumps | null, reach: Reach): string {
  const { frame, timeOf } = reach;
  const now = timeOf(frame);
  if (!appearance || !jumps || now === undefined) return "";
  const { last, next } = jumps;
  const then = timeOf(last ?? next ?? frame);
  if (then === undefined) return "";
  if (last !== null) return `Last seen at ${clockOf(then)} (${fixed((now - then) / 1e6, 1)} s ago)`;
  if (next !== null) return `First seen at ${clockOf(then)} (in ${fixed((then - now) / 1e6, 1)} s)`;
  return appearance.spans.length === 0 && !appearance.truncated ? "Not seen" : "";
}

/**
 * The body of an object's page: its class, details and motion from the sample on screen at once, then every field
 * the backend knows of it, in groups, the pinned ones first. While the next sample's fields are on their way, the view
 * repeats the summary and fields shown before, together, and the rows stay mounted; with none to repeat, `Loading…`
 * stands. An object that is not in the frame offers to go to where it was last and will next be. The strip marks the
 * object's events, which are listed under its fields; one opens with `onEvent`. The page's header, which names the
 * object, is the panel's.
 */
export function Inspector({
  inspection,
  memory,
  onMemory,
  reach,
  onEvent,
}: {
  inspection: Exclude<InspectionView, { state: "none" }>;
  /** What the viewer keeps of the inspector: pinned fields and folded groups. */
  memory: InspectorMemory;
  onMemory: (memory: InspectorMemory) => void;
  reach: Reach;
  /** Opens the page of one of the object's events. */
  onEvent: (event: EventRowWire, revision: number) => void;
}) {
  const { selection } = inspection;
  const appearance = useAppearance(reach.source, selection.lane, selection.entity);
  const absent = inspection.state === "absent";
  const summarized = absent ? inspection.last : inspection.summary;
  const fields = absent ? NO_FIELDS : inspection.fields;
  // Until the shown sample's fields arrive nothing is known, so no pin could say whether its field is missing.
  const unknown = inspection.state === "loading";
  const motion = absent ? null : inspection.summary.motion;
  const truncated = !absent && inspection.truncated;
  const access = absent ? undefined : inspection.access;
  const { frame, seek } = reach;
  const jumps = appearance ? jumpsFrom(appearance.spans, frame, appearance.truncated) : null;
  const when = absent ? whenOf(appearance, jumps, reach) : "";
  const filtered = absent && inspection.reason === "filtered";
  const pill = !absent ? motion : filtered ? "Hidden by filter" : "Not in this frame";
  const className = summarized ? (summarized.className ?? "unknown") : null;
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      {(summarized || pill || truncated) && (
        <div className="flex items-center gap-s border-b border-line py-xs pr-m pl-m">
          {className !== null && (
            <span
              aria-hidden
              className={`size-m shrink-0 rounded-full ${categoryDot(className)} ${absent ? "opacity-50" : ""}`}
            />
          )}
          <div className="flex min-w-0 flex-1 flex-wrap items-baseline gap-x-m">
            {className !== null && <span className="font-semibold break-words">{className}</span>}
            {summarized && !absent && detailOf(summarized) && (
              <span className="text-small text-muted">{detailOf(summarized)}</span>
            )}
          </div>
          {pill && <Chip>{pill}</Chip>}
          {truncated && !access && (
            <Chip tone="caution" title="Some fields are not listed">
              Truncated
            </Chip>
          )}
        </div>
      )}
      {appearance && (
        <div className="shrink-0 px-m py-s">
          <PresenceStrip
            spans={appearance.spans}
            frames={reach.frames}
            category={className ?? UNKNOWN_CLASS}
            ticks={[...new Set(appearance.events.map((event) => event.frame))]}
            current={frame}
            onSeek={seek}
          />
        </div>
      )}
      <div aria-busy={inspection.state === "loading"} className="flex min-h-0 flex-1 flex-col">
        {absent && (
          <p className="m-0 shrink-0 p-m text-small text-muted">
            {filtered
              ? "In this frame, but hidden by the overlay filter. Attributes are only available for objects that pass it."
              : `${when || "Not in the current frame"}. ${ATTRIBUTES_NOTE}`}
          </p>
        )}
        {absent && jumps && (jumps.last !== null || jumps.next !== null) && (
          <div className="flex shrink-0 flex-wrap gap-s px-m pb-m">
            {jumps.last !== null && (
              <Button size="small" disabled={!seek} onClick={() => seek?.(jumps.last as number)}>
                Go to last seen
              </Button>
            )}
            {jumps.next !== null && (
              <Button size="small" disabled={!seek} onClick={() => seek?.(jumps.next as number)}>
                Next appearance
              </Button>
            )}
          </div>
        )}
        {inspection.state === "unavailable" && (
          <p role="status" className="m-0 shrink-0 p-m text-muted">
            Details are not available
          </p>
        )}
        {unknown && (
          <p role="status" className="m-0 shrink-0 p-m text-muted">
            Loading…
          </p>
        )}
        {!unknown && (
          <InspectorLines
            onCopyRaw={access?.raw}
            onCopyLarge={access?.copy}
            onCopyStart={access?.cancel}
            fields={fields}
            pinFields={absent ? undefined : inspection.pins}
            entity={inspection.selection.entity}
            scope={`${inspection.selection.lane}:${inspection.selection.source}:${inspection.selection.entity}`}
            memory={memory}
            onMemory={onMemory}
          />
        )}
      </div>
      {!absent && inspection.error && (
        <p role="alert" className="p-s text-small text-danger">
          {inspection.error}
        </p>
      )}
      {access &&
        (access.next ||
          access.first ||
          access.progress ||
          access.commit ||
          access.error ||
          access.copied) && (
          <div className="flex shrink-0 flex-wrap items-center gap-s border-t border-line p-s">
            {access.first && (
              <Button size="small" data-cursor-id="inspection:first" onClick={access.first}>
                First fields
              </Button>
            )}
            {access.next && (
              <Button size="small" data-cursor-id="inspection:next" onClick={access.next}>
                Next fields
              </Button>
            )}
            {access.progress && (
              <>
                <span
                  role="progressbar"
                  aria-label="Copying value"
                  aria-valuemin={0}
                  aria-valuemax={access.progress.total}
                  aria-valuenow={access.progress.received}
                  className="text-small text-muted"
                >
                  Copying {Math.floor((100 * access.progress.received) / access.progress.total)}%
                </span>
                <Button size="small" data-cursor-id="inspection:cancel" onClick={access.cancel}>
                  Cancel
                </Button>
              </>
            )}
            {access.commit && (
              <>
                <Button size="small" data-cursor-id="inspection:copy" onClick={access.commit}>
                  Copy value
                </Button>
                <Button size="small" data-cursor-id="inspection:cancel" onClick={access.cancel}>
                  Cancel
                </Button>
              </>
            )}
            {access.error && (
              <span role="alert" className="text-small text-danger">
                {access.error}
              </span>
            )}
            {access.copied && (
              <span role="status" className="text-small text-muted">
                Copied
              </span>
            )}
          </div>
        )}
      {appearance && (
        <ObjectEvents
          events={appearance.events}
          truncated={appearance.eventsTruncated}
          timeOf={reach.timeOf}
          onOpen={(row) => onEvent(row, appearance.revision)}
        />
      )}
    </div>
  );
}
