import { useEffect, useId, useRef, type ReactNode } from "react";
import { Icon } from "./Icon";
import type { IconName } from "./icons";
import { ownKeys } from "./keys";
import { CHECKED, CHOSEN, CONTROL_IN, PILL, TONE, type Tone } from "./look";

/** Box of a status tag: the same shape as the interactive pills, with a clear border so the sizes match. */
const TAG = `${PILL} inline-flex min-h-row items-center rounded-control border border-transparent px-m`;

const chipKeys = ownKeys(
  new Set([" ", "Enter", "ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown"]),
);

interface ChipProps {
  tone?: Tone;
  /** Makes the chip a button; without it the chip is plain text that takes no focus. */
  onClick?: () => void;
  /** The full text when the chip is too narrow to show it. */
  title?: string;
  children: ReactNode;
}

/** A status tag whose text ends in an ellipsis when there is no room; clickable when given `onClick`. */
export function Chip({ tone = "neutral", onClick, title, children }: ChipProps) {
  const look = TONE[tone];
  const text = <span className="truncate">{children}</span>;
  if (!onClick)
    return (
      <span title={title} className={`${TAG} ${look.text} ${look.fill}`}>
        {text}
      </span>
    );
  return (
    <button
      type="button"
      title={title}
      onClick={onClick}
      onKeyDown={chipKeys}
      onKeyUp={chipKeys}
      className={`${TAG} ${look.text} ${look.fill} ${look.press} focus-ring`}
    >
      {text}
    </button>
  );
}

interface ToggleChipProps {
  checked: boolean;
  onChange: (checked: boolean) => void;
  disabled?: boolean;
  /** Shown in the leading slot while unchecked (a plus when omitted); checked always shows a check. */
  icon?: IconName;
  /** Why it is disabled, or what it does. */
  title?: string;
  children: ReactNode;
}

/** A chip that is checked or not on its own, with a checkbox's keyboard behavior and semantics. */
export function ToggleChip({
  checked,
  onChange,
  disabled,
  icon = "plus",
  title,
  children,
}: ToggleChipProps) {
  return (
    <label
      title={title}
      onKeyDown={chipKeys}
      onKeyUp={chipKeys}
      className={`${PILL} ${CONTROL_IN} ${CHECKED}`}
    >
      <input
        type="checkbox"
        checked={checked}
        disabled={disabled}
        onChange={(event) => onChange(event.target.checked)}
        className="sr-only"
      />
      <Icon name={checked ? "check" : icon} />
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
  /** Called when another option is chosen, and once with the first option when `value` is none of them. */
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
  preview,
}: ChoiceProps<T> & {
  className: string;
  itemClass: string;
  /** Drawn above an option's label. */
  preview?: (value: T) => ReactNode;
}) {
  const name = useId();
  const known = options.some(([option]) => option === value);
  const chosen = known ? value : options[0]?.[0];
  const report = useRef(onChange);
  report.current = onChange;
  useEffect(() => {
    // The first option is shown as chosen, so tell the parent instead of leaving it on a value nobody sees.
    if (!known && chosen !== undefined) report.current(chosen);
  }, [known, chosen]);
  return (
    <div
      role="radiogroup"
      aria-label={label}
      onKeyDown={chipKeys}
      onKeyUp={chipKeys}
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
          {preview?.(option)}
          <span className="truncate">{text}</span>
        </label>
      ))}
    </div>
  );
}

/** Chips of which exactly one is chosen, wrapping like a `ChipRow`. */
export function ChoiceChips<T extends string>(props: ChoiceProps<T>) {
  return (
    <Radios
      {...props}
      className="flex flex-wrap items-center gap-s"
      itemClass={`${PILL} ${CONTROL_IN} ${CHOSEN}`}
    />
  );
}

const SEGMENT =
  "inline-flex h-segment min-w-0 items-center rounded-control px-m text-small text-fg focus-ring-within not-has-checked:hover:bg-hover has-checked:bg-hover has-checked:text-accent";

/** Two or three joined segments of which exactly one is chosen: a setting that switches between modes; `wide` fills the width. */
export function SegmentedControl<T extends string>({
  wide = false,
  ...props
}: ChoiceProps<T> & { wide?: boolean }) {
  return (
    <Radios
      {...props}
      className={`${wide ? "flex w-full" : "inline-flex w-fit max-w-full"} gap-xs rounded-control border border-line-strong bg-panel p-xs`}
      itemClass={wide ? `${SEGMENT} flex-1 justify-center` : SEGMENT}
    />
  );
}

/** The chosen tile: a full accent edge that grows into the gap, so choosing one moves nothing, and a faint fill. */
const TILE_CHOSEN =
  "has-checked:-m-hairline has-checked:border-edge has-checked:border-accent has-checked:bg-accent/15 has-checked:font-semibold has-disabled:has-checked:border-line-strong/50";

const TILE = `${CONTROL_IN} ${TILE_CHOSEN} flex-col py-s [@media(max-height:20rem)]:px-s [@media(max-height:20rem)]:py-0`;

/** Cards of which exactly one is chosen, each a `preview` above its label: a choice that is easier to pick by looking. */
export function TileChoice<T extends string>({
  preview,
  ...props
}: ChoiceProps<T> & { preview: (value: T) => ReactNode }) {
  return (
    <Radios
      {...props}
      preview={preview}
      className="grid w-fit max-w-full grid-flow-col auto-cols-fr gap-m"
      itemClass={TILE}
    />
  );
}
