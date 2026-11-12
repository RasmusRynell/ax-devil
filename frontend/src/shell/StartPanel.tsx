import { shortcutDisplay } from "../kit/shortcut";
import { command, scopeOf } from "../client/commands";
import type { CommandId } from "../generated/settings";
import { Icon } from "../kit/Icon";
import type { IconName } from "../kit/icons";
import { KIND, QUIET, type Kind } from "../kit/look";
import { RowList } from "../kit/RowList";
import { useAllowed } from "../session/session";
import { useSettings } from "../client/settings";
import { RECENT_ID, recentLabel, usedOn, useRecents } from "../client/recents";

const OPEN: readonly [id: CommandId, icon: IconName, kind?: Kind][] = [
  ["open-workspace", "folder"],
  ["add-video", "square-play", "video"],
  ["add-live-stream", "video", "live"],
  ["add-playlist", "list-video", "playlist"],
];

/** The Workspace view's start panel: the ways to open content, and the recent workspaces. */
export function StartPanel({ onRun }: { onRun: (id: string) => void }) {
  const allowed = useAllowed();
  const recents = useRecents(true);
  const { shortcuts } = useSettings();
  return (
    <div className="flex flex-col gap-l">
      <div className="grid max-sm:grid-cols-2">
        {OPEN.map(([id, icon, kind]) => (
          <button
            key={id}
            type="button"
            data-cursor-id={`sidebar:${id}`}
            title={
              shortcuts[id]
                ? `${command(id).label} (${shortcutDisplay(shortcuts[id]).text})`
                : undefined
            }
            className={`${QUIET} py-xs text-left`}
            disabled={!allowed[scopeOf(id)]}
            onClick={() => onRun(id)}
          >
            <Icon name={icon} className={kind && allowed[scopeOf(id)] ? KIND[kind] : ""} />
            {command(id).label}
          </button>
        ))}
      </div>
      {recents.length > 0 && (
        <div className="[@media(max-height:20rem)]:hidden">
          <RowList
            heading="Recent workspaces"
            rows={recents.map(({ path, name, folder, usedMs }) => ({
              id: `${RECENT_ID}${path}`,
              icon: "folder",
              label: recentLabel(name),
              subtitle: folder,
              title: path,
              disabled: !allowed.host,
              trailing: <span className="shrink-0 text-muted">{usedOn(usedMs)}</span>,
            }))}
            onSelect={onRun}
          />
        </div>
      )}
    </div>
  );
}
