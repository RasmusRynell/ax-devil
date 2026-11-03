import { useId, type LabelHTMLAttributes, type ReactNode } from "react";
import { Icon } from "./Icon";
import { IconButton } from "./IconButton";
import { FIELD, FIELD_IN } from "./look";

interface SearchProps {
  placeholder: string;
  /** Makes the field controlled, with a clear button while it has text. */
  query?: { value: string; onChange: (value: string) => void };
}

/** A search input with a leading icon; the placeholder doubles as its accessible name. */
export function SearchField({ placeholder, query }: SearchProps) {
  return (
    <label className={`flex items-center gap-s ${FIELD_IN}`}>
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
}

/** A labelled drop-down over a closed set. */
export function Select<T extends string>({ label, value, options, onChange }: SelectProps<T>) {
  return (
    <label className={LABELLED}>
      {label}
      <select
        value={value}
        onChange={(event) => onChange(event.target.value as T)}
        className={`${FIELD} min-w-0`}
      >
        {options.map(([option, name]) => (
          <option key={option} value={option}>
            {name}
          </option>
        ))}
      </select>
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
}

/** A labelled on/off box; extra attributes go on its label, so the whole row is one target. */
export function Checkbox({
  label,
  checked,
  onChange,
  disabled,
  className = "",
  ...rest
}: CheckboxProps) {
  return (
    <label className={`${SPLIT} ${disabled ? "text-disabled" : ""} ${className}`} {...rest}>
      {label}
      <input
        type="checkbox"
        checked={checked}
        disabled={disabled}
        onChange={(event) => onChange(event.target.checked)}
        className="size-icon accent-accent"
      />
    </label>
  );
}

/** A labelled text box; `masked` hides what is typed, `placeholder` shows what applies while it is empty, `action` sits after the box, `describedBy` names a note about it. */
export function TextField({
  label,
  value,
  masked = false,
  placeholder,
  describedBy,
  action,
  onChange,
}: {
  label: ReactNode;
  value: string;
  placeholder?: string;
  masked?: boolean;
  describedBy?: string;
  action?: ReactNode;
  onChange: (value: string) => void;
}) {
  const id = useId();
  return (
    <div className={LABELLED}>
      <label htmlFor={id} className="contents">
        {label}
      </label>
      <span className="flex min-w-0 items-center gap-s">
        <input
          id={id}
          type={masked ? "password" : "text"}
          value={value}
          placeholder={placeholder}
          autoComplete="off"
          spellCheck={false}
          aria-describedby={describedBy}
          onChange={(event) => onChange(event.target.value)}
          className={`${FIELD} min-w-0 w-field`}
        />
        {action}
      </span>
    </div>
  );
}
