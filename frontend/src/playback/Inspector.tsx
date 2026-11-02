import { useState } from "react";
import { Icon } from "../kit/Icon";
import { Chip } from "../kit/Chips";
import { IconButton } from "../kit/IconButton";
import { SectionCaption } from "../kit/SectionCaption";
import type { InspectionView, Summary } from "./contract";
import type { FieldGroupWire, FieldWire } from "./protocol";

/** The groups in display order, with their headings. */
const GROUPS: readonly (readonly [FieldGroupWire, string])[] = [
  ["classification", "Classification"],
  ["motion", "Motion"],
  ["geometry", "Geometry"],
  ["debug", "Debug"],
  ["raw", "Raw"],
];

/** Who produced a Debug field, in the words the user knows. */
const ORIGINS = { source: "Source", decoder: "Computed" } as const;

/** What makes the inspector a column beside the picture rather than a cover over it: a wide enough viewer. */
const COLUMN =
  "@min-[72ch]:static @min-[72ch]:w-[min(var(--spacing-sidebar),60%)] @min-[72ch]:shrink-0 @min-[72ch]:border-l @min-[72ch]:border-line";
const COLUMN_CROWDED =
  "@min-[112ch]:static @min-[112ch]:w-[min(var(--spacing-sidebar),60%)] @min-[112ch]:shrink-0 @min-[112ch]:border-l @min-[112ch]:border-line";

/** A text longer than this many characters is cut until the user expands it. */
const CUT = 80;

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

/** One field's value: a button that copies it, with a long text cut until expanded. */
function Value({ value }: { value: FieldWire["value"] }) {
  const [expanded, setExpanded] = useState(false);
  const [copied, setCopied] = useState(false);
  const text = value === null ? "null" : String(value);
  const cut = text.length > CUT && !expanded;
  return (
    <>
      <button
        type="button"
        title="Copy"
        aria-label={`Copy ${text.slice(0, CUT)}`}
        onClick={() => {
          void navigator.clipboard?.writeText(text);
          setCopied(true);
        }}
        onBlur={() => setCopied(false)}
        className="inline-flex max-w-full items-start gap-s rounded-control bg-transparent p-0 text-left break-all text-fg hover:bg-hover focus-ring"
      >
        {cut ? text.slice(0, CUT) : text}
        {copied && <Icon name="check" />}
      </button>
      {text.length > CUT && (
        <button
          type="button"
          aria-expanded={expanded}
          onClick={() => setExpanded(!expanded)}
          className="ml-s rounded-control bg-transparent p-0 text-small text-muted hover:text-fg focus-ring"
        >
          {expanded ? "Show less" : "Show more"}
        </button>
      )}
    </>
  );
}

function Field({ field }: { field: FieldWire }) {
  return (
    <div className="flex flex-col">
      <dt className="flex flex-wrap items-baseline gap-s text-small break-all text-muted">
        {field.path.join(".") || "value"}
        {field.origin && <Chip>{ORIGINS[field.origin]}</Chip>}
      </dt>
      <dd className="m-0 break-words">
        <Value value={field.value} />
      </dd>
    </div>
  );
}

/** The groups that have fields, each as a section. */
function Groups({ fields }: { fields: readonly FieldWire[] }) {
  return GROUPS.map(([group, heading]) => {
    const own = fields.filter((field) => field.group === group);
    return (
      own.length > 0 && (
        <section key={group} aria-label={heading} className="-mx-m flex flex-col gap-s">
          <SectionCaption heading={heading} />
          <dl className="m-0 flex flex-col gap-s px-m">
            {own.map((field, i) => (
              <Field key={`${field.path.join(".")}:${i}`} field={field} />
            ))}
          </dl>
        </section>
      )
    );
  });
}

/**
 * The selected object: its summary from the sample on screen at once, then every field the backend knows of it, in
 * groups. While a sample's fields are on their way the last ones stay, under `Loading…`. A column beside the picture
 * when the viewer, a size container that is its nearest positioned ancestor, is wide enough for both; else it covers
 * the whole viewer, and Escape or the button closes it.
 */
export function Inspector({
  inspection,
  onClose,
  crowded = false,
}: {
  inspection: Exclude<InspectionView, { state: "none" }>;
  onClose: () => void;
  /** Whether another column is beside the picture, so that this one needs more room to be a column too. */
  crowded?: boolean;
}) {
  const summarized = inspection.state === "absent" ? null : inspection.summary;
  const fields = inspection.state === "absent" ? [] : inspection.fields;
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
        <IconButton icon="x" label="Close inspector" onClick={onClose} />
      </header>
      <div
        aria-busy={inspection.state === "loading"}
        className="flex min-h-0 flex-1 flex-col gap-m overflow-y-auto p-m"
      >
        {inspection.state === "absent" && <p className="m-0 text-muted">Not in this frame</p>}
        {inspection.state === "unavailable" && (
          <p role="status" className="m-0 text-muted">
            Details are not available
          </p>
        )}
        {inspection.state === "loading" && (
          <p role="status" className="m-0 text-muted">
            Loading…
          </p>
        )}
        <Groups fields={fields} />
        {inspection.state !== "absent" && inspection.truncated && (
          <p className="m-0 text-small text-muted">Some fields are not listed</p>
        )}
      </div>
    </aside>
  );
}
