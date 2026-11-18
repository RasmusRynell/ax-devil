import { ICONS, type IconName } from "./icons";

/** A decorative icon drawn in the current text color, or in the one a `className` such as a `KIND` sets. */
export function Icon({ name, className = "" }: { name: IconName; className?: string }) {
  return (
    <span
      aria-hidden
      className={`inline-block size-icon shrink-0 bg-current mask-contain mask-center mask-no-repeat ${className}`}
      style={{ maskImage: `url("${ICONS[name]}")` }}
    />
  );
}
