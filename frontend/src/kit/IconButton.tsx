import { shortcutDisplay } from "./shortcut";
import type { ButtonHTMLAttributes } from "react";
import { Icon } from "./Icon";
import type { IconName } from "./icons";
import { SQUARE } from "./look";

interface Props extends Omit<ButtonHTMLAttributes<HTMLButtonElement>, "children" | "title"> {
  icon: IconName;
  /** The accessible name and tooltip. */
  label: string;
  /** The key that does the same, added to the tooltip as in `Settings (Ctrl+,)`; the accessible name stays the label. */
  shortcut?: string | null;
  /** White on the near-black video scrim, instead of the theme's text color. */
  onCanvas?: boolean;
}

/* Square cornered with an inset focus ring, so an edge drawn on the button by a bar stays straight and the ring is not clipped at the window edge. */
const QUIET_ICON =
  "inline-flex min-h-row cursor-pointer items-center bg-transparent text-fg enabled:hover:bg-hover focus-ring-inset disabled:cursor-default disabled:text-disabled";
/** The same over the near-black video scrim: the scrim's text and hover tint; disabled, the icon dims to 40%. */
const ON_CANVAS =
  "inline-flex min-h-row cursor-pointer items-center bg-transparent text-on-canvas enabled:hover:bg-canvas-hover focus-ring-inset disabled:cursor-default disabled:opacity-40";

/** A borderless icon-only button, one row square; for bars, headers and video controls. */
export function IconButton({
  icon,
  label,
  shortcut,
  onCanvas = false,
  className = "",
  ...rest
}: Props) {
  return (
    <button
      type="button"
      aria-label={label}
      title={shortcut ? `${label} (${shortcutDisplay(shortcut).text})` : label}
      className={`${onCanvas ? ON_CANVAS : QUIET_ICON} ${SQUARE} shrink-0 ${className}`}
      {...rest}
    >
      <Icon name={icon} />
    </button>
  );
}
