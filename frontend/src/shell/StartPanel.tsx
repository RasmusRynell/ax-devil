import { command, scopeOf } from "../commands";
import type { CommandId } from "../generated/settings";
import { Icon } from "../kit/Icon";
import type { IconName } from "../kit/icons";
import { KIND, QUIET, type Kind } from "../kit/look";
import { RowList } from "../kit/RowList";
import { useAllowed } from "../session/session";
import { RECENT_ID, recentLabel, usedOn, useRecents } from "./recents";

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
  return (
    <div className="flex flex-col gap-l">
      <form
        className="grid max-sm:grid-cols-2"
        onSubmit={(event) => {
          event.preventDefault();
          onRun(OPEN[0][0]);
        }}
      >
        {OPEN.map(([id, icon, kind], at) => (
          <button
            key={id}
            // The first button is the form's default: Enter runs it, as the others run on their own click.
            type={at === 0 ? "submit" : "button"}
            data-cursor-id={`sidebar:${id}`}
            className={`${QUIET} py-xs text-left`}
            disabled={!allowed[scopeOf(id)]}
            onClick={at === 0 ? undefined : () => onRun(id)}
          >
            <Icon name={icon} className={kind && allowed[scopeOf(id)] ? KIND[kind] : ""} />
            {command(id).label}
          </button>
        ))}
      </form>
      {recents.length > 0 && (
        <div className="[@media(max-height:20rem)]:hidden">
          <RowList
            heading="Recent workspaces"
            rows={recents.map(({ path, name, usedMs }) => ({
              id: `${RECENT_ID}${path}`,
              label: recentLabel(name),
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
