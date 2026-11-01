import { useId, useState, type ReactNode } from "react";

/** A number the user edits as text; the change applies when they press Enter or leave the field. */
export function NumberField({
  label,
  value,
  min,
  max,
  step,
  placeholder,
  onCommit,
}: {
  label: string;
  value: number | null;
  min: number;
  max: number;
  step: number;
  /** What an empty field means, when it can be empty. */
  placeholder?: string;
  onCommit: (value: number | null) => void;
}) {
  const id = useId();
  const [draft, setDraft] = useState<string | null>(null);
  const commit = () => {
    if (draft === null) return;
    setDraft(null);
    const typed = draft.trim() === "" ? null : Number(draft);
    if (typed === null && placeholder === undefined) return;
    if (typed !== null && !Number.isFinite(typed)) return;
    const next = typed === null ? null : Math.min(max, Math.max(min, typed));
    if (next !== value) onCommit(next);
  };
  return (
    <div className="flex items-center justify-between gap-xl">
      <label htmlFor={id}>{label}</label>
      <input
        id={id}
        type="number"
        min={min}
        max={max}
        step={step}
        placeholder={placeholder}
        value={draft ?? (value === null ? "" : String(value))}
        onChange={(event) => setDraft(event.target.value)}
        onBlur={commit}
        onKeyDown={(event) => event.key === "Enter" && commit()}
        className="h-row w-[10ch] rounded-control border border-line-strong bg-popup px-s text-right text-fg tabular-nums placeholder:text-muted focus:border-accent focus:outline-none"
      />
    </div>
  );
}

/** A set of related controls under a name. */
export function Group({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div
      role="group"
      aria-label={label}
      className="flex flex-col gap-s border-t border-line pt-m first:border-t-0 first:pt-0"
    >
      {children}
    </div>
  );
}
