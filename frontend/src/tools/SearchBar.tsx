import { useLayoutEffect, useMemo, useRef } from "react";
import { Icon } from "../kit/Icon";
import { FIELD_IN } from "../kit/look";
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

/**
 * A search box for the query language of `parseSearch`: field names and operators are muted, and a word the language
 * cannot use (an incomplete term, a stray OR) is underlined. The text is typed in a transparent input over a colored
 * copy of itself.
 */
export function SearchBar({
  label,
  value,
  onChange,
}: {
  /** The accessible name and the placeholder. */
  label: string;
  value: string;
  onChange: (value: string) => void;
}) {
  const input = useRef<HTMLInputElement>(null);
  const copy = useRef<HTMLSpanElement>(null);
  const { tokens } = useMemo(() => parseSearch(value), [value]);
  // A long text scrolls inside the input; the copy follows it.
  const follow = () => {
    if (input.current && copy.current) copy.current.scrollLeft = input.current.scrollLeft;
  };
  useLayoutEffect(follow);
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
    <label className={`relative flex items-center gap-s ${FIELD_IN}`}>
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
          onScroll={follow}
          className="relative size-full bg-transparent text-transparent caret-fg outline-none placeholder:text-muted"
        />
      </span>
    </label>
  );
}
