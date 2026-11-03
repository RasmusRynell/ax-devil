import {
  memo,
  useCallback,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type MouseEvent,
} from "react";
import { ContextMenu } from "../kit/ContextMenu";
import { IconButton } from "../kit/IconButton";
import type { MenuEntry } from "../kit/Menu";
import { SectionCaption } from "../kit/SectionCaption";
import { useRowWindow } from "../shell/useRowWindow";
import {
  copyValue,
  displayValue,
  fieldKey,
  inspectorLines,
  rawJson,
  togglePin,
  type InspectorMemory,
  type Line,
} from "./fields";
import type { FieldGroupWire, FieldWire } from "./protocol";

const ROW = '[role="listitem"]';
const ROW_HEIGHT = "var(--spacing-row)";
/** Names the Raw caption's copy button in the one `copied` key; no field key can equal it. */
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
}: FieldProps) {
  return (
    <div
      role="listitem"
      data-field={field}
      data-index={index}
      className="group/row grid h-row grid-cols-[auto_minmax(0,3fr)_minmax(0,2fr)_auto] items-center gap-s px-s hover:bg-hover"
    >
      <IconButton
        icon="pin"
        label={`${pinned ? "Unpin" : "Pin"} ${label}`}
        data-act="pin"
        className={pinned ? "" : ON_ROW}
      />
      <span title={label} className="truncate text-small text-muted">
        {label}
      </span>
      {missing ? (
        <span className="col-span-2 truncate text-muted">unavailable</span>
      ) : (
        <>
          <span title={full} className="truncate tabular-nums">
            {shown}
          </span>
          <IconButton
            icon={copied ? "check" : "copy"}
            label={`Copy ${label}`}
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
  folded: boolean;
  copyable: boolean;
  copied: boolean;
  onFold: (group: FieldGroupWire) => void;
}

/** A group's heading with its fold control and, for Raw, its copy button; a caption inside a group is plain text. */
const CaptionLine = memo(function CaptionLine({
  group,
  heading,
  folded,
  copyable,
  copied,
  onFold,
}: CaptionProps) {
  return (
    <div role="listitem" data-group={group ?? undefined} className="group/row flex items-center">
      {group ? (
        <div className="min-w-0 flex-1">
          <SectionCaption
            heading={heading}
            fold={{ open: !folded, onToggle: () => onFold(group) }}
          />
        </div>
      ) : (
        <span className="flex h-row flex-1 items-center px-m text-small text-muted">{heading}</span>
      )}
      {copyable && (
        <IconButton
          icon={copied ? "check" : "copy"}
          label={`Copy ${heading}`}
          data-act="copy"
          className={copied ? "" : ON_ROW}
        />
      )}
    </div>
  );
});

/** The rows of `lines` as elements; `pins` says which fields show as pinned. */
function rows(
  lines: readonly Line[],
  first: number,
  pins: ReadonlySet<string>,
  copied: string | null,
  onFold: (group: FieldGroupWire) => void,
  keyOf: (line: Line, index: number) => string | number,
) {
  return lines.map((line, i) => {
    if (line.kind === "caption") {
      return (
        <CaptionLine
          key={keyOf(line, i)}
          group={line.fold}
          heading={line.heading}
          folded={line.folded}
          copyable={line.copyable}
          copied={copied === RAW_COPIED}
          onFold={onFold}
        />
      );
    }
    const found = line.kind === "field";
    return (
      <FieldLine
        key={keyOf(line, i)}
        field={line.key}
        index={first + i}
        label={line.label}
        shown={found ? displayValue(line.value) : ""}
        full={found ? copyValue(line.value) : ""}
        pinned={pins.has(line.key)}
        copied={copied === String(first + i)}
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
  memory,
  onMemory,
}: {
  fields: readonly FieldWire[];
  memory: InspectorMemory;
  onMemory: (memory: InspectorMemory) => void;
}) {
  const { pins, folded } = memory;
  const { pinned, lines } = useMemo(
    () => inspectorLines(fields, pins, folded),
    [fields, pins, folded],
  );
  const pinnedKeys = useMemo(() => new Set(pins.map(fieldKey)), [pins]);
  const byKey = useMemo(() => {
    const found = new Map<string, Line>();
    for (const line of [...pinned, ...lines])
      if (line.kind !== "caption" && !found.has(line.key)) found.set(line.key, line);
    return found;
  }, [pinned, lines]);
  const all = useMemo(() => [...pinned, ...lines], [pinned, lines]);
  const [copied, setCopied] = useState<string | null>(null);
  const [menuKey, setMenuKey] = useState<string | null>(null);
  const { ref, start, end, update } = useRowWindow(lines.length, ROW);

  const copy = (text: string, id: string) => {
    void navigator.clipboard?.writeText(text);
    setCopied(id);
  };
  const pin = (key: string) => {
    const line = byKey.get(key);
    if (line && line.kind !== "caption") onMemory({ ...memory, pins: togglePin(pins, line.id) });
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
  const onClick = (event: MouseEvent<HTMLDivElement>) => {
    const button = (event.target as Element).closest<HTMLElement>("[data-act]");
    const row = button?.closest<HTMLElement>("[data-field],[data-group]");
    if (!button || !row) return;
    const { field, group } = row.dataset;
    if (button.dataset.act === "pin" && field !== undefined) pin(field);
    else if (button.dataset.act === "copy") {
      const index = row.dataset.index;
      const line = index === undefined ? undefined : all[Number(index)];
      if (line?.kind === "field") copy(copyValue(line.value), index as string);
      else if (group === "raw") copy(rawJson(fields), RAW_COPIED);
    }
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
          onClick={onClick}
          onContextMenu={onContextMenu}
          onBlur={() => setCopied(null)}
        >
          {pinned.length > 0 && (
            <>
              <section aria-label="Pinned" className="shrink-0">
                <SectionCaption heading="Pinned" count={pinned.length} />
                <div
                  role="list"
                  className="max-h-[calc(var(--spacing-row)*6)] overflow-y-auto [scrollbar-gutter:stable]"
                >
                  {rows(pinned, 0, pinnedKeys, copied, onFold, (line) =>
                    line.kind === "caption" ? "" : line.key,
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
                lines.slice(start, end),
                pinned.length + start,
                pinnedKeys,
                copied,
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
