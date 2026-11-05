import {
  memo,
  useCallback,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
  type KeyboardEvent,
  type MouseEvent,
} from "react";
import { ContextMenu } from "../kit/ContextMenu";
import { IconButton } from "../kit/IconButton";
import type { MenuEntry } from "../kit/Menu";
import { MiddleElided } from "../kit/MiddleElided";
import { SectionCaption } from "../kit/SectionCaption";
import { useRowWindow } from "../shell/useRowWindow";
import {
  copyValue,
  displayValue,
  fieldKey,
  hexOf,
  inspectorLines,
  rawJson,
  togglePin,
  type InspectorMemory,
  type Line,
} from "./fields";
import type { FieldGroupWire, FieldWire } from "./protocol";
import { useCopied, type Copied } from "./useCopied";

const ROW = '[role="listitem"]';
const ROW_HEIGHT = "var(--spacing-row)";
/** Names the Raw caption's copy button in the one "last copied" mark; no field's id is this. */
const RAW_COPIED = "raw";
/** Hidden until the row is pointed at or holds focus; a pinned row keeps its pin showing. */
const ON_ROW = "opacity-0 group-hover/row:opacity-100 group-focus-within/row:opacity-100";

interface FieldProps {
  /** The field's group and path; several rows can share it. */
  field: string;
  /** This row's place in the pinned rows then the list's, which says which row a click is on. */
  index: number;
  label: string;
  shown: string;
  full: string;
  pinned: boolean;
  copied: boolean;
  missing: boolean;
  /** The colour a colour field's value is the score of. */
  rgb?: readonly [number, number, number];
  /** How much of a bar the value fills, 0 to 1; no bar when absent. */
  fraction?: number;
  /** Where a value too long for its row loses its middle; it loses its end when absent. */
  elide?: "middle";
  /** Whether the field can be pinned; the object's id cannot. */
  pinnable: boolean;
}

/** The most characters of a long value, such as an id, that the label column leaves room for where it can. */
const VALUE_ROOM = 20;
/** An average label character as a share of the label's text size. */
const LABEL_CHAR = 0.55;
/** What a row's pin and copy columns and the gaps between its four columns take of its width. */
const AROUND = "(2 * var(--spacing-row) + 3 * var(--spacing-s))";

/**
 * The label column's width, as the rows' `--label`: as wide as the longest label, but at least a third of the room
 * and leaving a long value its room where it can, so a value never starves beside a wide label.
 */
function labelWidth(lines: readonly Line[]): string {
  let plain = 0;
  let swatched = 0;
  let value = 0;
  for (const line of lines) {
    if (line.kind === "unavailable") plain = Math.max(plain, line.label.length);
    if (line.kind !== "field") continue;
    if (line.rgb) swatched = Math.max(swatched, line.label.length);
    else plain = Math.max(plain, line.label.length);
    value = Math.max(value, (line.text ?? displayValue(line.value)).length);
  }
  const text = (chars: number) => `${chars * LABEL_CHAR} * var(--text-small)`;
  const longest = `max(${text(plain)}, ${text(swatched)} + var(--spacing-m) + var(--spacing-s))`;
  const inner = `(100% - ${AROUND})`;
  const room = `${Math.min(value, VALUE_ROOM)}ch`;
  return `min(${longest}, max(${inner} / 3, ${inner} - var(--spacing-s) - ${room}))`;
}

/** A value on one line, cut at its end, or in its middle when `middle` says so; the whole of it is the title. */
function Shown({ text, title, middle }: { text: string; title: string; middle: boolean }) {
  if (middle)
    return <MiddleElided text={text} title={title} className="flex-1 font-mono tabular-nums" />;
  return (
    <span title={title} className="min-w-0 flex-1 truncate font-mono tabular-nums">
      {text}
    </span>
  );
}

/** One field: pin, label, value and copy. Stateless; the list handles the clicks. */
const FieldLine = memo(function FieldLine({
  field,
  index,
  label,
  shown,
  full,
  pinned,
  copied,
  missing,
  rgb,
  fraction,
  elide,
  pinnable,
}: FieldProps) {
  return (
    <div
      role="listitem"
      data-field={field}
      data-index={index}
      tabIndex={-1}
      className="group/row focus-ring-inset grid h-row grid-cols-[auto_minmax(0,var(--label))_minmax(0,1fr)_auto] items-center gap-s px-s hover:bg-hover"
    >
      {pinnable ? (
        <IconButton
          icon="pin"
          label={`${pinned ? "Unpin" : "Pin"} ${label}`}
          data-act="pin"
          className={pinned ? "" : ON_ROW}
        />
      ) : (
        <span aria-hidden className="square-row" />
      )}
      <span title={label} className="flex min-w-0 items-center gap-s text-small text-muted">
        {rgb && (
          <span
            aria-hidden
            title={hexOf(rgb)}
            style={{ backgroundColor: `rgb(${rgb.join(" ")})` }}
            className="size-m shrink-0 rounded-mark border border-line"
          />
        )}
        <span className="truncate">{label}</span>
      </span>
      {missing ? (
        <span className="col-span-2 truncate text-muted italic">unavailable</span>
      ) : (
        <>
          <span className="@container/value flex min-w-0 items-center gap-m">
            <Shown text={shown} title={full} middle={elide === "middle"} />
            {fraction !== undefined && (
              <span
                aria-hidden
                className="hidden h-s w-[10ch] shrink-0 rounded-mark bg-line @min-[20ch]/value:block"
              >
                <span
                  className="block h-full rounded-mark bg-accent"
                  style={{ width: `${fraction * 100}%` }}
                />
              </span>
            )}
          </span>
          <IconButton
            icon={copied ? "check" : "copy"}
            label={`${copied ? "Copied" : "Copy"} ${label}`}
            data-act="copy"
            className={copied ? "" : ON_ROW}
          />
        </>
      )}
    </div>
  );
});

interface CaptionProps {
  group: FieldGroupWire | null;
  heading: string;
  count?: number;
  folded: boolean;
  copyable: boolean;
  copied: boolean;
  onFold: (group: FieldGroupWire) => void;
}

/** A group's heading with its fold control and, for Raw, its copy button; a caption inside a group is plain text. */
const CaptionLine = memo(function CaptionLine({
  group,
  heading,
  count,
  folded,
  copyable,
  copied,
  onFold,
}: CaptionProps) {
  return (
    <div
      role="listitem"
      data-group={group ?? undefined}
      tabIndex={-1}
      className="group/row focus-ring-inset flex items-center"
    >
      {group ? (
        <div className="min-w-0 flex-1">
          <SectionCaption
            heading={heading}
            count={count}
            fold={{ open: !folded, onToggle: () => onFold(group) }}
          />
        </div>
      ) : (
        <span className="flex h-row flex-1 items-center px-m text-small text-muted">{heading}</span>
      )}
      {copyable && (
        <IconButton
          icon={copied ? "check" : "copy"}
          label={`${copied ? "Copied" : "Copy"} ${heading}`}
          data-act="copy"
          className={copied ? "" : ON_ROW}
        />
      )}
    </div>
  );
});

/**
 * What tells each field row of the pinned rows then the list's from the others, for the copy mark: its section, the
 * caption it sits under (which says a Debug field's origin), group and path, and which of the rows sharing them it is
 * out of how many. It follows the field when rows come and go, unlike a position, and a mark does not move to another
 * row when a duplicate comes or goes.
 */
function rowIds(pinned: readonly Line[], lines: readonly Line[]): string[] {
  const ids: string[] = [];
  for (const [section, part] of [pinned, lines].entries()) {
    const bases: string[] = [];
    const counts = new Map<string, number>();
    let under = "";
    for (const line of part) {
      if (line.kind === "caption") under = line.heading;
      if (line.kind !== "field") {
        bases.push("");
        continue;
      }
      const base = `${section}:${under}:${line.key}`;
      bases.push(base);
      counts.set(base, (counts.get(base) ?? 0) + 1);
    }
    const seen = new Map<string, number>();
    for (const base of bases) {
      if (base === "") {
        ids.push("");
        continue;
      }
      const n = seen.get(base) ?? 0;
      seen.set(base, n + 1);
      ids.push(`${base}#${n}/${counts.get(base)}`);
    }
  }
  return ids;
}

/** The rows of `lines` as elements; `pins` says which fields show as pinned. */
function rows(
  fields: readonly FieldWire[],
  lines: readonly Line[],
  first: number,
  pins: ReadonlySet<string>,
  copied: Copied,
  ids: readonly string[],
  onFold: (group: FieldGroupWire) => void,
  keyOf: (line: Line, index: number) => string | number,
) {
  return lines.map((line, i) => {
    if (line.kind === "heading") {
      return (
        <div
          key={keyOf(line, i)}
          role="listitem"
          className="flex h-row items-center px-m text-small text-muted"
        >
          <span className="truncate">{line.heading}</span>
        </div>
      );
    }
    if (line.kind === "caption") {
      return (
        <CaptionLine
          key={keyOf(line, i)}
          group={line.fold}
          heading={line.heading}
          count={line.count}
          folded={line.folded}
          copyable={line.copyable}
          copied={copied.is(RAW_COPIED, () => rawJson(fields))}
          onFold={onFold}
        />
      );
    }
    const found = line.kind === "field";
    const full = found ? copyValue(line) : "";
    return (
      <FieldLine
        key={keyOf(line, i)}
        field={line.key}
        index={first + i}
        label={line.label}
        shown={found ? (line.text ?? displayValue(line.value)) : ""}
        full={full}
        rgb={found ? line.rgb : undefined}
        fraction={found ? line.fraction : undefined}
        elide={found ? line.elide : undefined}
        pinnable={line.id !== null}
        pinned={pins.has(line.key)}
        copied={found && copied.is(ids[first + i] ?? "", full)}
        missing={!found}
      />
    );
  });
}

/**
 * An object's fields: the pinned ones above a separator, then every group with its fields in a windowed, one-line-row
 * list. Pins follow a field's group and path from object to object; `memory` is the viewer's, so they and the folded
 * groups outlast the object shown. Clicks are handled here, by the row's data attributes, so rows hold no state.
 */
export function InspectorLines({
  fields,
  entity,
  scope,
  memory,
  onMemory,
}: {
  fields: readonly FieldWire[];
  /** The object's id, which leads the list. */
  entity: string;
  /** What the object is, lane and all; a copy mark does not outlast it. */
  scope: string;
  memory: InspectorMemory;
  onMemory: (memory: InspectorMemory) => void;
}) {
  const { pins, folded } = memory;
  const { pinned, lines } = useMemo(
    () => inspectorLines(fields, pins, folded, entity),
    [fields, pins, folded, entity],
  );
  const pinnedKeys = useMemo(() => new Set(pins.map(fieldKey)), [pins]);
  const byKey = useMemo(() => {
    const found = new Map<string, Extract<Line, { kind: "field" | "unavailable" }>>();
    for (const line of [...pinned, ...lines])
      if ((line.kind === "field" || line.kind === "unavailable") && !found.has(line.key))
        found.set(line.key, line);
    return found;
  }, [pinned, lines]);
  const all = useMemo(() => [...pinned, ...lines], [pinned, lines]);
  const label = useMemo(() => labelWidth(all), [all]);
  const [menuKey, setMenuKey] = useState<string | null>(null);
  const { ref, start, end, update } = useRowWindow(lines.length, ROW);

  const copied = useCopied(scope);
  const ids = useMemo(() => rowIds(pinned, lines), [pinned, lines]);
  const pin = (key: string) => {
    const line = byKey.get(key);
    if (line?.id) onMemory({ ...memory, pins: togglePin(pins, line.id) });
  };
  const latest = useRef({ memory, onMemory });
  useLayoutEffect(() => {
    latest.current = { memory, onMemory };
  });
  const onFold = useCallback((group: FieldGroupWire) => {
    const { memory: now, onMemory: report } = latest.current;
    const next = new Set(now.folded);
    if (!next.delete(group)) next.add(group);
    report({ ...now, folded: next });
  }, []);
  /** Copies what the row's copy button would; false for a row with nothing to copy. */
  const copyRow = (row: HTMLElement): boolean => {
    const { index, group } = row.dataset;
    const line = index === undefined ? undefined : all[Number(index)];
    if (line?.kind === "field") copied.copy(ids[Number(index)] ?? "", copyValue(line));
    else if (group === "raw") copied.copy(RAW_COPIED, rawJson(fields));
    else return false;
    return true;
  };
  const onClick = (event: MouseEvent<HTMLDivElement>) => {
    const button = (event.target as Element).closest<HTMLElement>("[data-act]");
    const row = button?.closest<HTMLElement>("[data-field],[data-group]");
    if (!button || !row) return;
    const { field } = row.dataset;
    if (button.dataset.act === "pin" && field !== undefined) pin(field);
    else if (button.dataset.act === "copy") copyRow(row);
  };
  /** Ctrl+C on the row that holds focus copies its field, or the Raw group; a text the user selected is theirs. */
  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.key.toLowerCase() !== "c" || !(event.ctrlKey || event.metaKey)) return;
    if (event.altKey || event.shiftKey || window.getSelection()?.toString()) return;
    const row = (event.target as Element).closest<HTMLElement>("[data-field],[data-group]");
    if (row && copyRow(row)) event.preventDefault();
  };
  const onContextMenu = (event: MouseEvent<HTMLDivElement>) => {
    const key = (event.target as Element).closest<HTMLElement>("[data-field]")?.dataset.field;
    // Off a field the browser's own menu stays.
    if (key === undefined) event.stopPropagation();
    else setMenuKey(key);
  };
  const entries: MenuEntry[] =
    menuKey === null
      ? []
      : [
          {
            kind: "action",
            id: "pin",
            label: pinnedKeys.has(menuKey) ? "Unpin field" : "Pin field",
          },
        ];

  return (
    <div className="grid min-h-0 flex-1 grid-rows-[minmax(0,1fr)]">
      <ContextMenu entries={entries} onSelect={() => menuKey !== null && pin(menuKey)}>
        <div
          className="flex h-full min-h-0 flex-col"
          style={{ "--label": label } as CSSProperties}
          onClick={onClick}
          onContextMenu={onContextMenu}
          onKeyDown={onKeyDown}
        >
          {pinned.length > 0 && (
            <>
              <section aria-label="Pinned" className="shrink-0">
                <SectionCaption heading="Pinned" count={pinned.length} />
                <div
                  role="list"
                  className="max-h-[calc(var(--spacing-row)*6)] overflow-y-auto [scrollbar-gutter:stable]"
                >
                  {rows(fields, pinned, 0, pinnedKeys, copied, ids, onFold, (line) =>
                    line.kind === "field" || line.kind === "unavailable" ? line.key : "",
                  )}
                </div>
              </section>
              <hr className="m-0 border-0 border-t border-line" />
            </>
          )}
          <div
            ref={ref}
            onScroll={update}
            className="min-h-0 flex-1 overflow-y-auto [scrollbar-gutter:stable]"
          >
            <div
              role="list"
              style={{
                paddingTop: `calc(${ROW_HEIGHT} * ${start})`,
                paddingBottom: `calc(${ROW_HEIGHT} * ${lines.length - end})`,
              }}
            >
              {rows(
                fields,
                lines.slice(start, end),
                pinned.length + start,
                pinnedKeys,
                copied,
                ids,
                onFold,
                (_, i) => start + i,
              )}
            </div>
          </div>
        </div>
      </ContextMenu>
    </div>
  );
}
