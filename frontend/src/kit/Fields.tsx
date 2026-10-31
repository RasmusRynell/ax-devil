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
