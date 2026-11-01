import type { ButtonHTMLAttributes } from "react";
import { Icon } from "./Icon";
import type { IconName } from "./icons";

interface Props extends Omit<ButtonHTMLAttributes<HTMLButtonElement>, "children" | "title"> {
  icon: IconName;
  /** The accessible name and tooltip. */
  label: string;
  /** White on the near-black video scrim, instead of the theme's text color. */
  onCanvas?: boolean;
}

/** An icon-only button with a neutral hover tint; the caller sizes it. */
export function IconButton({ icon, label, onCanvas = false, className = "", ...rest }: Props) {
  const tone = onCanvas
    ? "text-on-canvas hover:bg-canvas-hover"
    : "text-fg hover:bg-hover disabled:text-disabled disabled:hover:bg-transparent";
  return (
    <button
      type="button"
      aria-label={label}
      title={label}
      className={`flex shrink-0 items-center justify-center bg-transparent focus-ring-inset ${tone} ${className}`}
      {...rest}
    >
      <Icon name={icon} />
    </button>
  );
}
