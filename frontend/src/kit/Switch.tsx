import type { ReactNode } from "react";

/** A labelled on/off switch: the setting takes effect as it flips, and its state shows in the control itself. */
export function Switch({
  label,
  checked,
  onChange,
}: {
  label: ReactNode;
  checked: boolean;
  onChange: (checked: boolean) => void;
}) {
  return (
    <label className="flex items-center justify-between gap-xl">
      {label}
      <input
        type="checkbox"
        role="switch"
        aria-label={typeof label === "string" ? label : undefined}
        checked={checked}
        onChange={(event) => onChange(event.target.checked)}
        className="relative h-icon w-[calc(var(--spacing-icon)*2)] shrink-0 appearance-none rounded-full border border-muted bg-transparent checked:border-accent checked:bg-accent focus-ring before:absolute before:top-1/2 before:left-xs before:size-[calc(var(--spacing-icon)-var(--spacing-m))] before:-translate-y-1/2 before:rounded-full before:bg-muted before:transition-transform checked:before:translate-x-[calc(var(--spacing-icon)+var(--spacing-m)-var(--spacing-hairline)*2-var(--spacing-xs)*2)] checked:before:bg-popup motion-reduce:before:transition-none"
      />
    </label>
  );
}
