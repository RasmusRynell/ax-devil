import { useId, type KeyboardEvent, type ReactNode } from "react";

/** A small outlined tag; every chip shares this shape so mixed rows line up. */
const CHIP =
  "inline-flex max-w-full min-w-0 items-center rounded-control border px-m py-xs text-small whitespace-nowrap";

/** What a chosen or checked control looks like: the accent edge and tint, with the text kept readable. */
const TINT = "has-checked:bg-accent/20";
const CHECKED = `${TINT} has-checked:border-accent`;
/** The look of a chip you can check; hover tints only while it is enabled, and a disabled one is dashed. */
const CHECKABLE = `${CHIP} ${CHECKED} border-line-strong text-fg has-enabled:hover:bg-hover has-disabled:border-dashed has-disabled:text-disabled focus-ring-within`;

const OWN_KEYS = new Set([" ", "Enter", "ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown"]);

/**
 * Keeps a control's own keys from reaching the shell's shortcuts, so Space on a chip chooses it instead of playing
 * the video and an arrow key moves the choice instead of stepping a frame.
 */
function ownKeys(event: KeyboardEvent) {
  if (OWN_KEYS.has(event.key) && !event.ctrlKey && !event.altKey && !event.metaKey)
    event.stopPropagation();
}

export type ChipKind = "neutral" | "success" | "warning" | "error";

const KINDS: Record<ChipKind, { rest: string; hover: string }> = {
  neutral: { rest: "border-line-strong text-muted", hover: "hover:bg-hover" },
  success: { rest: "border-success text-success", hover: "hover:bg-success/15" },
  warning: { rest: "border-caution text-caution", hover: "hover:bg-caution/15" },
  error: { rest: "border-danger text-danger", hover: "hover:bg-danger/15" },
};

interface ChipProps {
  kind?: ChipKind;
  /** Makes the chip a button; without it the chip is plain text that takes no focus. */
  onClick?: () => void;
  /** The full text when the chip is too narrow to show it. */
  title?: string;
  children: ReactNode;
}

/** A status tag whose text ends in an ellipsis when there is no room; clickable when given `onClick`. */
export function Chip({ kind = "neutral", onClick, title, children }: ChipProps) {
  const look = KINDS[kind];
  const text = <span className="truncate">{children}</span>;
  if (!onClick)
    return (
      <span title={title} className={`${CHIP} ${look.rest}`}>
        {text}
      </span>
    );
  return (
    <button
      type="button"
      title={title}
      onClick={onClick}
      onKeyDown={ownKeys}
      onKeyUp={ownKeys}
      className={`${CHIP} ${look.rest} ${look.hover} bg-transparent focus-ring`}
    >
      {text}
    </button>
  );
}

interface ToggleChipProps {
  checked: boolean;
  onChange: (checked: boolean) => void;
  disabled?: boolean;
  /** Why it is disabled, or what it does. */
  title?: string;
  children: ReactNode;
}

/** A chip that is checked or not on its own, with a checkbox's keyboard behavior and semantics. */
export function ToggleChip({ checked, onChange, disabled, title, children }: ToggleChipProps) {
  return (
    <label title={title} onKeyDown={ownKeys} onKeyUp={ownKeys} className={CHECKABLE}>
      <input
        type="checkbox"
        checked={checked}
        disabled={disabled}
        onChange={(event) => onChange(event.target.checked)}
        className="sr-only"
      />
      <span className="truncate">{children}</span>
    </label>
  );
}

/** Chips in the order given, wrapping whole to the next line; never scrolls or cuts one. */
export function ChipRow({ label, children }: { label?: string; children: ReactNode }) {
  return (
    <div
      role={label ? "group" : undefined}
      aria-label={label}
      className="flex flex-wrap items-center gap-s"
    >
      {children}
    </div>
  );
}

interface ChoiceProps<T extends string> {
  /** The accessible name of the group. */
  label: string;
  /** Each choice as `[value, label]`. */
  options: readonly (readonly [T, string])[];
  /** The chosen value; the first option when it is none of them, so one is always chosen. */
  value: T;
  /** Called only when another option is chosen. */
  onChange: (value: T) => void;
}

/**
 * Native radio buttons behind labels: Tab lands on the chosen one, the arrow keys move and choose, choosing the
 * current one does nothing, and Enter still submits an enclosing form, which a plain button would not.
 */
function Radios<T extends string>({
  label,
  options,
  value,
  onChange,
  className,
  itemClass,
}: ChoiceProps<T> & { className: string; itemClass: string }) {
  const name = useId();
  const chosen = options.some(([option]) => option === value) ? value : options[0]?.[0];
  return (
    <div
      role="radiogroup"
      aria-label={label}
      onKeyDown={ownKeys}
      onKeyUp={ownKeys}
      className={className}
    >
      {options.map(([option, text]) => (
        <label key={option} className={itemClass}>
          <input
            type="radio"
            name={name}
            value={option}
            checked={option === chosen}
            onChange={() => onChange(option)}
            className="sr-only"
          />
          <span className="truncate">{text}</span>
        </label>
      ))}
    </div>
  );
}

/** Chips of which exactly one is chosen, wrapping like a `ChipRow`. */
export function ChoiceChips<T extends string>(props: ChoiceProps<T>) {
  return <Radios {...props} className="flex flex-wrap items-center gap-s" itemClass={CHECKABLE} />;
}

const SEGMENT = `flex h-row min-w-0 items-center px-m ${TINT} text-fg hover:bg-hover focus-ring-within -outline-offset-(--stroke-focus)`;

/** Two or three joined segments of which exactly one is chosen: a setting that switches between modes. */
export function SegmentedControl<T extends string>(props: ChoiceProps<T>) {
  return (
    <Radios
      {...props}
      className="inline-flex max-w-full divide-x divide-line-strong overflow-hidden rounded-control border border-line-strong"
      itemClass={SEGMENT}
    />
  );
}
