import { useId, type LabelHTMLAttributes, type ReactNode } from "react";
import { Icon } from "./Icon";

/** A search input with a leading icon; the placeholder doubles as its accessible name. */
export function SearchField({ placeholder }: { placeholder: string }) {
  return (
    <label className="flex h-row items-center gap-s rounded-control border border-line-strong px-m text-muted focus-within:border-accent">
      <Icon name="search" />
      <input
        type="search"
        aria-label={placeholder}
        placeholder={placeholder}
        className="min-w-0 flex-1 bg-transparent text-fg outline-none placeholder:text-muted"
      />
    </label>
  );
}

const FIELD = "flex items-center justify-between gap-xl";
const INPUT =
  "h-row rounded-control border border-line-strong bg-popup px-s text-fg focus:border-accent focus:outline-none";

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
    <label className={FIELD}>
      {label}
      <select
        value={value}
        onChange={(event) => onChange(event.target.value as T)}
        className={INPUT}
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
    <label className={`${FIELD} ${disabled ? "text-disabled" : ""} ${className}`} {...rest}>
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
    <div className={FIELD}>
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
          className={`${INPUT} min-w-0 w-field`}
        />
        {action}
      </span>
    </div>
  );
}
