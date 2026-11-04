import { useId, type LabelHTMLAttributes, type ReactNode } from "react";
import { Icon } from "./Icon";
import { IconButton } from "./IconButton";
import { FIELD, FIELD_IN } from "./look";

interface SearchProps {
  placeholder: string;
  /** Makes the field controlled, with a clear button while it has text. */
  query?: { value: string; onChange: (value: string) => void };
  /** Sizes the field in its row. */
  className?: string;
}

/** A search input with a leading icon; the placeholder doubles as its accessible name. */
export function SearchField({ placeholder, query, className = "" }: SearchProps) {
  return (
    <label className={`flex items-center gap-s ${FIELD_IN} ${className}`}>
      <span className="flex text-muted">
        <Icon name="search" />
      </span>
      <input
        type="search"
        aria-label={placeholder}
        placeholder={placeholder}
        value={query?.value}
        onChange={query && ((event) => query.onChange(event.target.value))}
        // In a dialog, Enter is OK; here it only finishes typing.
        onKeyDown={(event) => event.key === "Enter" && event.preventDefault()}
        className="min-w-0 flex-1 bg-transparent text-fg outline-none placeholder:text-muted"
      />
      {query?.value && (
        <IconButton icon="x" label="Clear search" onClick={() => query.onChange("")} />
      )}
    </label>
  );
}

const SPLIT = "flex items-center justify-between gap-xl";
/** A label with its control; at a width too narrow for both on a line, the control drops under the label. */
const LABELLED = "flex flex-wrap items-center justify-between gap-x-xl gap-y-s";

interface SelectProps<T extends string> {
  label: ReactNode;
  value: T;
  /** Each choice as `[value, label]`. */
  options: readonly (readonly [T, string])[];
  onChange: (value: T) => void;
  disabled?: boolean;
  /** Sits after the drop-down. */
  action?: ReactNode;
  /** With `action`, the drop-down gets the width of a wide text box. */
  wide?: boolean;
}

/** A labelled drop-down over a closed set. */
export function Select<T extends string>({
  label,
  value,
  options,
  onChange,
  disabled,
  action,
  wide = false,
}: SelectProps<T>) {
  const select = (
    <select
      value={value}
      disabled={disabled}
      onChange={(event) => onChange(event.target.value as T)}
      className={`${FIELD} min-w-0 ${action ? (wide ? "w-field-wide" : "w-field") : ""}`}
    >
      {options.map(([option, name]) => (
        <option key={option} value={option}>
          {name}
        </option>
      ))}
    </select>
  );
  return (
    <label className={LABELLED}>
      {label}
      {action ? (
        <span className="flex min-w-0 items-center gap-s">
          {select}
          {action}
        </span>
      ) : (
        select
      )}
    </label>
  );
}

interface CheckboxProps extends Omit<
  LabelHTMLAttributes<HTMLLabelElement>,
  "onChange" | "children"
> {
  label: ReactNode;
  checked: boolean;
  onChange: (checked: boolean) => void;
  disabled?: boolean;
  /** The box comes before the label, which then takes only its own width. */
  leading?: boolean;
}

/** A labelled on/off box, the box at the far end of its row; extra attributes go on its label, so the whole row is one target. */
export function Checkbox({
  label,
  checked,
  onChange,
  disabled,
  leading = false,
  className = "",
  ...rest
}: CheckboxProps) {
  const box = (
    <input
      type="checkbox"
      checked={checked}
      disabled={disabled}
      onChange={(event) => onChange(event.target.checked)}
      className="size-icon shrink-0 accent-accent"
    />
  );
  return (
    <label
      className={`${leading ? "flex items-center gap-m" : SPLIT} ${disabled ? "text-disabled" : ""} ${className}`}
      {...rest}
    >
      {leading && box}
      {label}
      {!leading && box}
    </label>
  );
}

/** A labelled text box; `masked` hides what is typed, `placeholder` shows what applies while it is empty, `suggestions` are offered to pick from while any text stays allowed, `action` sits after the box, `describedBy` names a note about it, `fill` gives the box its own row at the full width, `wide` makes it wide enough for a `$NAME` reference. */
export function TextField({
  label,
  value,
  masked = false,
  placeholder,
  suggestions,
  describedBy,
  action,
  fill = false,
  wide = false,
  onChange,
}: {
  label: ReactNode;
  value: string;
  placeholder?: string;
  masked?: boolean;
  suggestions?: readonly string[];
  describedBy?: string;
  action?: ReactNode;
  fill?: boolean;
  wide?: boolean;
  onChange: (value: string) => void;
}) {
  const id = useId();
  const list = `${id}-suggestions`;
  return (
    <div className={LABELLED}>
      <label htmlFor={id} className="contents">
        {label}
      </label>
      <span className={`flex min-w-0 items-center gap-s ${fill ? "w-full" : ""}`}>
        <input
          id={id}
          type={masked ? "password" : "text"}
          value={value}
          placeholder={placeholder}
          autoComplete="off"
          spellCheck={false}
          list={suggestions?.length ? list : undefined}
          aria-describedby={describedBy}
          onChange={(event) => onChange(event.target.value)}
          className={`${FIELD} min-w-0 ${fill ? "flex-1" : wide ? "w-field-wide" : "w-field"}`}
        />
        {suggestions?.length ? (
          <datalist id={list}>
            {suggestions.map((suggestion) => (
              <option key={suggestion} value={suggestion} />
            ))}
          </datalist>
        ) : null}
        {action}
      </span>
    </div>
  );
}
