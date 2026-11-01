import type { ReactNode } from "react";
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

/** A labelled on/off box. */
export function Checkbox({
  label,
  checked,
  onChange,
}: {
  label: ReactNode;
  checked: boolean;
  onChange: (checked: boolean) => void;
}) {
  return (
    <label className={FIELD}>
      {label}
      <input
        type="checkbox"
        checked={checked}
        onChange={(event) => onChange(event.target.checked)}
        className="size-icon accent-accent"
      />
    </label>
  );
}

/** A labelled text box; `masked` hides what is typed. */
export function TextField({
  label,
  value,
  masked = false,
  onChange,
}: {
  label: ReactNode;
  value: string;
  masked?: boolean;
  onChange: (value: string) => void;
}) {
  return (
    <label className={FIELD}>
      {label}
      <input
        type={masked ? "password" : "text"}
        value={value}
        autoComplete="off"
        spellCheck={false}
        onChange={(event) => onChange(event.target.value)}
        className={`${INPUT} min-w-0 w-field`}
      />
    </label>
  );
}
