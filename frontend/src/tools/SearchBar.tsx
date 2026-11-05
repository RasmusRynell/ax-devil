import {
  useCallback,
  useEffect,
  useImperativeHandle,
  useLayoutEffect,
  useMemo,
  useRef,
  type Ref,
} from "react";
import { Icon } from "../kit/Icon";
import { IconButton } from "../kit/IconButton";
import { FIELD_IN } from "../kit/look";
import { Popover } from "../kit/Popover";
import { parseSearch, type SearchTokenKind } from "../search/query";

/** How each kind of word is drawn. Same glyph widths as the text, so the colored copy lines up under the caret. */
const LOOK: Record<SearchTokenKind, string> = {
  text: "",
  field: "text-muted",
  operator: "text-muted",
  value: "",
  or: "text-muted",
  not: "text-muted",
  invalid: "underline decoration-wavy",
};

/** What to type and what it does, one row each: any path of an object's data can be searched, so no field names. */
const SYNTAX = [
  ["human 17", "text in id, class or state"],
  ["class:human", "field equals value"],
  ["score>0.7", "compare with > < >= <= ="],
  ["-state:parked", "exclude matches"],
  ["a OR b", "either term"],
  ["a b", "both terms (default)"],
] as const;

/**
 * A search box for the query language of `parseSearch`: field names and operators are muted, and a word the language
 * cannot use (an incomplete term, a stray OR) is underlined. The text is typed in a transparent input over a colored
 * copy of itself. `onChange` follows every keystroke; Return and Clear also say `onCommit`, for the owner to apply the
 * text at once instead of when typing stops. Clear shows only with text and leaves the field focused; `handle` lets
 * the owner focus the field too.
 */
export function SearchBar({
  handle,
  label,
  value,
  onChange,
  onCommit,
}: {
  /** Gives the owner a way to focus the field. */
  handle?: Ref<{ focus(): void }>;
  /** The accessible name and the placeholder. */
  label: string;
  value: string;
  onChange: (value: string) => void;
  /** Return was pressed or the field was cleared: `value` is the text to apply now. */
  onCommit: (value: string) => void;
}) {
  const input = useRef<HTMLInputElement>(null);
  const copy = useRef<HTMLSpanElement>(null);
  useImperativeHandle(handle, () => ({ focus: () => input.current?.focus() }), []);
  const { tokens } = useMemo(() => parseSearch(value), [value]);
  // A long text scrolls inside the input; the copy follows it.
  const follow = useCallback(() => {
    if (input.current && copy.current) copy.current.scrollLeft = input.current.scrollLeft;
  }, []);
  // The copy is rebuilt only when the text changes; the input's own scrolling is followed by `onScroll`.
  useLayoutEffect(follow, [follow, value]);
  let at = 0;
  const parts = tokens.flatMap(({ start, end, kind }) => {
    const part = [
      value.slice(at, start),
      <span key={start} className={LOOK[kind]}>
        {value.slice(start, end)}
      </span>,
    ];
    at = end;
    return part;
  });
  return (
    <div className="flex items-center gap-s">
      <label className={`relative flex min-w-0 flex-1 items-center gap-s font-mono ${FIELD_IN}`}>
        <span className="flex text-muted">
          <Icon name="search" />
        </span>
        <span className="relative h-full min-w-0 flex-1">
          <span
            ref={copy}
            aria-hidden
            className="pointer-events-none absolute inset-0 flex items-center overflow-hidden text-fg"
          >
            <span className="whitespace-pre">
              {parts}
              {value.slice(at)}
            </span>
          </span>
          <input
            ref={input}
            type="text"
            value={value}
            aria-label={label}
            placeholder={label}
            autoComplete="off"
            spellCheck={false}
            onChange={(event) => onChange(event.target.value)}
            onKeyDown={(event) => {
              // Return that confirms an input-method candidate is not the user's Return.
              if (event.key === "Enter" && !event.nativeEvent.isComposing && event.keyCode !== 229)
                onCommit(event.currentTarget.value);
            }}
            onScroll={follow}
            className="relative size-full bg-transparent text-transparent caret-fg outline-none placeholder:text-muted"
          />
        </span>
        {value !== "" && (
          <IconButton
            icon="x"
            label="Clear"
            onClick={() => {
              onChange("");
              onCommit("");
              input.current?.focus();
            }}
          />
        )}
      </label>
      <Popover
        label="Query syntax"
        trigger={<Icon name="circle-help" />}
        className="size-row justify-center"
      >
        <SyntaxSheet />
      </Popover>
    </div>
  );
}

/** The syntax rows. They hold no control for the popover to focus, so the sheet takes focus itself when it opens. */
function SyntaxSheet() {
  const sheet = useRef<HTMLDListElement>(null);
  useEffect(() => sheet.current?.focus(), []);
  return (
    <dl
      ref={sheet}
      tabIndex={0}
      className="m-0 grid grid-cols-[max-content_max-content] gap-x-l gap-y-s focus-ring-inset"
    >
      {SYNTAX.map(([example, meaning]) => (
        <div key={example} className="contents">
          <dt className="font-mono">{example}</dt>
          <dd className="m-0 text-small text-muted">{meaning}</dd>
        </div>
      ))}
    </dl>
  );
}
