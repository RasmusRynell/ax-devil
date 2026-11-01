import type { LabelHTMLAttributes } from "react";
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

interface SelectProps<T extends string> {
  label: string;
  value: T;
  options: readonly T[];
  onChange: (value: T) => void;
}

/** A labelled drop-down over a closed set; options show with a capital first letter. */
export function Select<T extends string>({ label, value, options, onChange }: SelectProps<T>) {
  return (
    <label className="flex items-center justify-between gap-xl">
      {label}
      <select
        value={value}
        onChange={(event) => onChange(event.target.value as T)}
        className="h-row rounded-control border border-line-strong bg-popup px-s text-fg focus:border-accent focus:outline-none"
      >
        {options.map((option) => (
          <option key={option} value={option}>
            {option.charAt(0).toUpperCase() + option.slice(1)}
          </option>
        ))}
      </select>
    </label>
  );
}

interface TextFieldProps {
  label: string;
  value: string;
  onChange: (value: string) => void;
  type?: "text" | "password";
}

/** A labelled single-line input with its label above, as wide as its container up to a readable width. */
export function TextField({ label, value, onChange, type = "text" }: TextFieldProps) {
  return (
    <label className="flex flex-col gap-s">
      {label}
      <input
        type={type}
        value={value}
        onChange={(event) => onChange(event.target.value)}
        autoComplete="off"
        spellCheck={false}
        className="h-row w-field max-w-full rounded-control border border-line-strong bg-transparent px-m text-fg focus:border-accent focus:outline-none"
      />
    </label>
  );
}

interface CheckboxProps extends Omit<
  LabelHTMLAttributes<HTMLLabelElement>,
  "onChange" | "children"
> {
  label: string;
  checked: boolean;
  onChange: (checked: boolean) => void;
}

/** A labelled check box; extra attributes go on its label, so the whole row is one target. */
export function Checkbox({ label, checked, onChange, className = "", ...rest }: CheckboxProps) {
  return (
    <label className={`flex min-h-row items-center gap-m px-m py-xs ${className}`} {...rest}>
      <input
        type="checkbox"
        checked={checked}
        onChange={(event) => onChange(event.target.checked)}
        className="size-icon shrink-0 accent-accent"
      />
      {label}
    </label>
  );
}
