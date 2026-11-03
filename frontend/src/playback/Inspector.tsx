import { Chip } from "../kit/Chips";
import { IconButton } from "../kit/IconButton";
import type { InspectionView, Summary } from "./contract";
import type { InspectorMemory } from "./fields";
import { InspectorLines } from "./InspectorLines";
import type { FieldWire } from "./protocol";

/** What makes the inspector a column beside the picture rather than a cover over it: a wide enough viewer. */
const COLUMN =
  "@min-[72ch]:static @min-[72ch]:w-[min(var(--spacing-sidebar),60%)] @min-[72ch]:shrink-0 @min-[72ch]:border-l @min-[72ch]:border-line";
const COLUMN_CROWDED =
  "@min-[112ch]:static @min-[112ch]:w-[min(var(--spacing-sidebar),60%)] @min-[112ch]:shrink-0 @min-[112ch]:border-l @min-[112ch]:border-line";

const NO_FIELDS: readonly FieldWire[] = [];

const decimals = (value: number): string => String(Number(value.toFixed(2)));

/** `"<id> <class>"`, as the hover chip says it. */
const titleOf = ({ entity, className }: Summary): string =>
  className === null ? entity : `${entity} ${className}`;

/** `"score S · confidence C"`, leaving out what is missing. */
function detailOf({ score, confidence }: Summary): string {
  const parts: string[] = [];
  if (score !== null) parts.push(`score ${decimals(score)}`);
  if (confidence !== null) parts.push(`confidence ${decimals(confidence)}`);
  return parts.join(" · ");
}

/**
 * The selected object: its summary from the sample on screen at once, then every field the backend knows of it, in
 * groups, the pinned ones first. While a sample's fields are on their way the last ones stay, under `Loading…`. A column beside the picture
 * when the viewer, a size container that is its nearest positioned ancestor, is wide enough for both; else it covers
 * the whole viewer, and Escape or the button closes it.
 */
export function Inspector({
  inspection,
  onClose,
  crowded = false,
  memory,
  onMemory,
}: {
  inspection: Exclude<InspectionView, { state: "none" }>;
  onClose: () => void;
  /** Whether another column is beside the picture, so that this one needs more room to be a column too. */
  crowded?: boolean;
  /** What the viewer keeps of the inspector: pinned fields and folded groups. */
  memory: InspectorMemory;
  onMemory: (memory: InspectorMemory) => void;
}) {
  const summarized = inspection.state === "absent" ? null : inspection.summary;
  const fields = inspection.state === "absent" ? NO_FIELDS : inspection.fields;
  // Before any fields arrive nothing is known, so no pin could say whether its field is missing.
  const unknown = inspection.state === "loading" && fields.length === 0;
  return (
    <aside
      aria-label="Inspector"
      className={`absolute inset-0 z-10 flex flex-col bg-panel text-fg ${crowded ? COLUMN_CROWDED : COLUMN}`}
    >
      <header className="flex items-center gap-s border-b border-line py-xs pr-xs pl-m">
        <div className="flex min-w-0 flex-1 flex-wrap items-baseline gap-x-m">
          <h2 className="m-0 font-semibold break-words">
            {summarized ? titleOf(summarized) : inspection.selection.entity}
          </h2>
          {summarized && detailOf(summarized) && (
            <span className="text-small text-muted">{detailOf(summarized)}</span>
          )}
        </div>
        {inspection.state !== "absent" && inspection.truncated && (
          <Chip tone="caution" title="Some fields are not listed">
            Truncated
          </Chip>
        )}
        <IconButton icon="x" label="Close inspector" onClick={onClose} />
      </header>
      <div aria-busy={inspection.state === "loading"} className="flex min-h-0 flex-1 flex-col">
        {inspection.state === "absent" && (
          <p className="m-0 shrink-0 p-m text-muted">Not in this frame</p>
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
        {!unknown && <InspectorLines fields={fields} memory={memory} onMemory={onMemory} />}
      </div>
    </aside>
  );
}
