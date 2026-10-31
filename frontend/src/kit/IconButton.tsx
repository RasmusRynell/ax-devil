import type { ButtonHTMLAttributes } from "react";
import { Icon } from "./Icon";
import type { IconName } from "./icons";

interface Props extends Omit<ButtonHTMLAttributes<HTMLButtonElement>, "children" | "title"> {
  icon: IconName;
  /** The accessible name and tooltip. */
  label: string;
}

/** An icon-only button with a neutral hover tint; the caller sizes it. */
export function IconButton({ icon, label, className = "", ...rest }: Props) {
  return (
    <button
      type="button"
      aria-label={label}
      title={label}
      className={`flex shrink-0 items-center justify-center bg-transparent text-fg hover:bg-hover focus-ring-inset ${className}`}
      {...rest}
    >
      <Icon name={icon} />
    </button>
  );
}
