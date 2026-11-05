import { Button } from "../kit/Button";
import { Chip } from "../kit/Chips";
import { fixed } from "../present/expr/fixed";
import { categoryDot } from "../tools/category";
import { jumpsFrom, type Jumps } from "./appearanceJumps";
import type { Appearance } from "./appearances";
import type { InspectionView, Summary } from "./contract";
import type { InspectorMemory } from "./fields";
import { InspectorLines } from "./InspectorLines";
import type { FieldWire } from "./protocol";
import type { PlaybackView } from "./view";
import { useAppearance, type AppearanceSource } from "./useAppearance";

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
  timeOf: (frame) => source.frameTime(frame),
  seek: canSeek ? (frame) => source.seek(frame) : undefined,
});

/** A frame time as `HH:MM:SS.mmm`, the clock of its UTC day (the video position for a recording). */
function clockOf(us: number): string {
  const part = (value: number, width: number): string => String(value).padStart(width, "0");
  const hours = Math.floor(us / 3_600_000_000) % 24;
  const minutes = Math.floor(us / 60_000_000) % 60;
  const seconds = Math.floor(us / 1_000_000) % 60;
  return `${part(hours, 2)}:${part(minutes, 2)}:${part(seconds, 2)}.${part(Math.floor((us % 1_000_000) / 1000), 3)}`;
}

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
 * the backend knows of it, in groups, the pinned ones first. While a sample's fields are on their way there are none,
 * under `Loading…`. An object that is not in the frame offers to go to where it was last and will next be. The
 * page's header, which names the object, is the panel's.
 */
export function Inspector({
  inspection,
  memory,
  onMemory,
  reach,
}: {
  inspection: Exclude<InspectionView, { state: "none" }>;
  /** What the viewer keeps of the inspector: pinned fields and folded groups. */
  memory: InspectorMemory;
  onMemory: (memory: InspectorMemory) => void;
  reach: Reach;
}) {
  const { selection } = inspection;
  const appearance = useAppearance(reach.source, selection.lane, selection.entity);
  const absent = inspection.state === "absent";
  const summarized = absent ? inspection.last : inspection.summary;
  const fields = absent ? NO_FIELDS : inspection.fields;
  // Until the sample's fields arrive nothing is known, so no pin could say whether its field is missing.
  const unknown = inspection.state === "loading";
  const motion = absent ? null : inspection.summary.motion;
  const truncated = !absent && inspection.truncated;
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
          {truncated && (
            <Chip tone="caution" title="Some fields are not listed">
              Truncated
            </Chip>
          )}
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
        {inspection.state === "loading" && (
          <p role="status" className="m-0 shrink-0 p-m text-muted">
            Loading…
          </p>
        )}
        {!unknown && (
          <InspectorLines
            fields={fields}
            entity={inspection.selection.entity}
            scope={`${inspection.selection.lane}:${inspection.selection.source}:${inspection.selection.entity}`}
            memory={memory}
            onMemory={onMemory}
          />
        )}
      </div>
    </div>
  );
}
