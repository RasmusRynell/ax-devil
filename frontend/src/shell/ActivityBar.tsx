import { IconButton } from "../kit/IconButton";
import { ACTIVITY, type ActivityItem } from "./views";

interface Props {
  /** The view shown in the open sidebar, or null while it is hidden. */
  active: string | null;
  onSelect: (item: ActivityItem) => void;
}

/** The left-edge icon strip: views at the top, other windows at the bottom. */
export function ActivityBar({ active, onSelect }: Props) {
  const button = (item: ActivityItem) => (
    <IconButton
      key={item.id}
      icon={item.icon}
      label={item.label}
      className="size-activity border-l-2 border-transparent aria-pressed:border-accent"
      aria-pressed={item.opens === "view" ? active === item.id : undefined}
      onClick={() => onSelect(item)}
    />
  );
  return (
    <nav
      aria-label="Activity"
      className="flex shrink-0 flex-col justify-between border-r border-line bg-panel"
    >
      <div className="flex flex-col">{ACTIVITY.top.map(button)}</div>
      <div className="flex flex-col">{ACTIVITY.bottom.map(button)}</div>
    </nav>
  );
}
