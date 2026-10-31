import type { ButtonHTMLAttributes } from "react";
import { Icon } from "./Icon";
import type { IconName } from "./icons";

interface Props extends ButtonHTMLAttributes<HTMLButtonElement> {
  icon?: IconName;
}

/** The neutral action button: transparent at rest, a subtle tint on hover; the same for every kind of action. */
export function Button({ icon, children, className = "", type = "button", ...rest }: Props) {
  return (
    <button
      type={type}
      className={`inline-flex min-h-row items-center gap-m rounded-control bg-transparent px-m py-xs text-left text-fg hover:bg-hover focus-ring disabled:text-disabled disabled:hover:bg-transparent ${className}`}
      {...rest}
    >
      {icon && <Icon name={icon} />}
      {children}
    </button>
  );
}
