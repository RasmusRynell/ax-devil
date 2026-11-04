import { Icon } from "./Icon";
import type { IconName } from "./icons";
import { CONTROL, SQUARE } from "./look";

/** An icon-only square that is on or off by itself (`aria-pressed`); `label` names it and is its tooltip. */
export function IconToggle({
  icon,
  label,
  pressed,
  onChange,
}: {
  icon: IconName;
  label: string;
  pressed: boolean;
  onChange: (pressed: boolean) => void;
}) {
  return (
    <button
      type="button"
      aria-pressed={pressed}
      aria-label={label}
      title={label}
      onClick={() => onChange(!pressed)}
      className={`${CONTROL} ${SQUARE} shrink-0 border-line-strong text-fg aria-[pressed=false]:enabled:hover:bg-hover aria-pressed:border-accent/50 aria-pressed:bg-accent/15 aria-pressed:enabled:hover:bg-accent/25`}
    >
      <Icon name={icon} />
    </button>
  );
}
