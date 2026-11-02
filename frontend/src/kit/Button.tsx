import { useRef, useState, type ButtonHTMLAttributes, type ReactNode } from "react";
import { Icon } from "./Icon";
import type { IconName } from "./icons";
import { CONTROL, EDGE, SQUARE } from "./look";
import { Menu, type MenuCommand, type MenuEntry } from "./Menu";
import { Popup } from "./Popup";

interface BaseProps extends Omit<ButtonHTMLAttributes<HTMLButtonElement>, "children" | "title"> {
  /** `small` is the compact action beside a field or in a row: small text at the same height. */
  size?: "small";
}

type Props = BaseProps &
  (
    | { icon?: IconName; children: ReactNode; label?: never }
    | { icon: IconName; label: string; children?: never }
  );

/**
 * The outlined action button; `type="submit"`, the Enter action, carries the accent edge. With `label` and no
 * children it is icon-only: a square whose accessible name and tooltip are the label.
 */
export function Button({
  icon,
  label,
  size,
  children,
  className = "",
  type = "button",
  ...rest
}: Props) {
  const look = `${CONTROL} ${type === "submit" ? EDGE.default : EDGE.rest}`;
  return (
    <button
      type={type}
      aria-label={label}
      title={label}
      className={`${look} ${label === undefined ? "" : SQUARE} ${size === "small" ? "text-small" : ""} ${className}`}
      {...rest}
    >
      {icon && <Icon name={icon} />}
      {children}
    </button>
  );
}

const NOTHING_CHECKED: ReadonlySet<string> = new Set();

interface MenuButtonProps {
  /** The button's text, shown before the chevron. */
  label: string;
  entries: readonly MenuEntry[];
  /** The ids of the `check` entries that are checked. */
  checked?: ReadonlySet<string>;
  onSelect: (command: MenuCommand) => void;
  disabled?: boolean;
}

/** A button that opens `entries` as a menu below it at once, closing on a choice or Escape and returning focus to it. */
export function MenuButton({
  label,
  entries,
  checked = NOTHING_CHECKED,
  onSelect,
  disabled,
}: MenuButtonProps) {
  const [open, setOpen] = useState(false);
  const wrap = useRef<HTMLSpanElement>(null);
  return (
    <span ref={wrap} className="relative inline-block">
      <Button
        size="small"
        aria-haspopup="menu"
        aria-expanded={open}
        disabled={disabled}
        onClick={() => setOpen((now) => !now)}
      >
        {label}
        <Icon name="chevron-down" />
      </Button>
      {open && (
        <Popup
          boundary={wrap}
          onClose={() => setOpen(false)}
          className="absolute top-full left-0 z-10 min-w-max"
        >
          <Menu
            entries={entries}
            checked={checked}
            onSelect={(command) => {
              setOpen(false);
              wrap.current?.querySelector("button")?.focus();
              onSelect(command);
            }}
          />
        </Popup>
      )}
    </span>
  );
}

/** The square folder button after a path field; `what` names the thing chosen, as in "Choose video file". */
export function BrowseButton({ what, onBrowse }: { what: string; onBrowse: () => void }) {
  return <Button icon="folder" label={`Choose ${what}`} onClick={onBrowse} />;
}
