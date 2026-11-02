import { command, scopeOf } from "../commands";
import type { CommandId } from "../generated/settings";
import { Icon } from "../kit/Icon";
import type { IconName } from "../kit/icons";
import { QUIET } from "../kit/look";
import { SectionCaption } from "../kit/SectionCaption";
import { useAllowed } from "../session/session";

const OPEN: readonly [id: CommandId, icon: IconName][] = [
  ["open-workspace", "folder"],
  ["add-video", "square-play"],
  ["add-live-stream", "video"],
  ["add-playlist", "list-video"],
];

/** The Workspace view's start panel: the ways to open content, and the recent workspaces. */
export function StartPanel({ onRun }: { onRun: (id: string) => void }) {
  const allowed = useAllowed();
  return (
    <div className="flex flex-col gap-l">
      <div className="grid max-sm:grid-cols-2">
        {OPEN.map(([id, icon]) => (
          <button
            key={id}
            type="button"
            data-cursor-id={`sidebar:${id}`}
            className={`${QUIET} py-xs text-left`}
            disabled={!allowed[scopeOf(id)]}
            onClick={() => onRun(id)}
          >
            <Icon name={icon} />
            {command(id).label}
          </button>
        ))}
      </div>
      <section className="flex flex-col gap-xs [@media(max-height:20rem)]:hidden">
        <SectionCaption heading="Recent workspaces" />
        <p className="m-0 px-m text-muted">No recent workspaces</p>
      </section>
    </div>
  );
}
