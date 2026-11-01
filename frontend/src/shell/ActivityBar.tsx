import { scopeOf } from "../commands";
import { IconButton } from "../kit/IconButton";
import { useAllowed } from "../session/session";
import { ACTIVITY, type ActivityItem } from "./views";

interface Props {
  /** The view shown in the open sidebar, or null while it is hidden. */
  active: string | null;
  onSelect: (item: ActivityItem) => void;
}

/** The left-edge icon strip: views at the top, other windows at the bottom. */
export function ActivityBar({ active, onSelect }: Props) {
  const allowed = useAllowed();
  const button = (item: ActivityItem) => (
    <IconButton
      key={item.id}
      icon={item.icon}
      data-cursor-id={`activity:${item.id}`}
      label={item.label}
      disabled={!allowed[item.opens === "view" ? "view" : scopeOf(item.id)]}
      className="size-activity border-transparent aria-pressed:border-accent max-sm:border-b-edge sm:border-l-edge"
      aria-pressed={item.opens === "view" ? active === item.id : undefined}
      onClick={() => onSelect(item)}
    />
  );
  return (
    <nav
      aria-label="Activity"
      data-cursor-region="activityBar"
      className="flex shrink-0 justify-between border-b border-line bg-panel sm:flex-col sm:border-r sm:border-b-0"
    >
      <div className="flex sm:flex-col">{ACTIVITY.top.map(button)}</div>
      <div className="flex sm:flex-col">{ACTIVITY.bottom.map(button)}</div>
    </nav>
  );
}
