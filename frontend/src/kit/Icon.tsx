import { ICONS, type IconName } from "./icons";

/** A decorative icon drawn in the current text color. */
export function Icon({ name }: { name: IconName }) {
  return (
    <span
      aria-hidden
      className="inline-block size-icon shrink-0 bg-current mask-contain mask-center mask-no-repeat"
      style={{ maskImage: `url("${ICONS[name]}")` }}
    />
  );
}
