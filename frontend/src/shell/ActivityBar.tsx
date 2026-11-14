import { scopeOf } from "../client/commands";
import type { CommandId } from "../generated/settings";
import { Icon } from "../kit/Icon";
import { IconButton } from "../kit/IconButton";
import { usePluginListing, usePluginViews } from "../plugins/listing";
import { useAllowed } from "../session/session";
import { useSettings } from "../client/settings";
import { ACTIVITY, pluginItem, type ActivityItem } from "./views";

interface Props {
  /** The view shown in the open sidebar, or null while it is hidden. */
  active: string | null;
  onSelect: (item: ActivityItem) => void;
}

/**
 * The left-edge icon strip: views at the top, plugins' after them, other windows at the bottom. A view that needs attention carries a
 * warning mark on its icon and says why in its tooltip: Plugins, while any plugin failed to load.
 */
export function ActivityBar({ active, onSelect }: Props) {
  const allowed = useAllowed();
  const { failed } = usePluginListing();
  const plugins = usePluginViews("sidebar").map(pluginItem);
  const { shortcuts } = useSettings();
  const warnings: Record<string, string> =
    failed > 0 ? { plugins: `${failed} failed to load` } : {};
  const button = (item: ActivityItem) => {
    const warning = warnings[item.id];
    return (
      <span key={item.id} className="relative flex">
        <IconButton
          icon={item.icon}
          data-cursor-id={`activity:${item.id}`}
          label={warning ? `${item.label}: ${warning}` : item.label}
          shortcut={item.opens === "command" ? shortcuts[item.id as CommandId] : null}
          disabled={!allowed[item.opens === "view" ? "view" : scopeOf(item.id)]}
          className="size-activity border-transparent aria-pressed:border-accent max-sm:border-b-edge sm:border-l-edge"
          aria-pressed={item.opens === "view" ? active === item.id : undefined}
          onClick={() => onSelect(item)}
        />
        {warning && (
          <Icon
            name="triangle-alert"
            className="pointer-events-none absolute right-xs bottom-xs scale-60 text-caution"
          />
        )}
      </span>
    );
  };
  return (
    <nav
      aria-label="Activity"
      data-cursor-region="activityBar"
      className="flex shrink-0 justify-between border-b border-line bg-panel sm:flex-col sm:border-r sm:border-b-0"
    >
      <div className="flex sm:flex-col">{[...ACTIVITY.top, ...plugins].map(button)}</div>
      <div className="flex sm:flex-col">{ACTIVITY.bottom.map(button)}</div>
    </nav>
  );
}
