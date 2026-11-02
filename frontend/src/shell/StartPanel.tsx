import { command, scopeOf } from "../commands";
import type { CommandId } from "../generated/settings";
import { Icon } from "../kit/Icon";
import type { IconName } from "../kit/icons";
import { KIND, QUIET, type Kind } from "../kit/look";
import { SectionCaption } from "../kit/SectionCaption";
import { useAllowed } from "../session/session";

const OPEN: readonly [id: CommandId, icon: IconName, kind?: Kind][] = [
  ["open-workspace", "folder"],
  ["add-video", "square-play", "video"],
  ["add-live-stream", "video", "live"],
  ["add-playlist", "list-video", "playlist"],
];

/** The Workspace view's start panel: the ways to open content, and the recent workspaces. */
export function StartPanel({ onRun }: { onRun: (id: string) => void }) {
  const allowed = useAllowed();
  return (
    <div className="flex flex-col gap-l">
      <div className="grid max-sm:grid-cols-2">
        {OPEN.map(([id, icon, kind]) => (
          <button
            key={id}
            type="button"
            data-cursor-id={`sidebar:${id}`}
            className={`${QUIET} py-xs text-left`}
            disabled={!allowed[scopeOf(id)]}
            onClick={() => onRun(id)}
          >
            <Icon name={icon} className={kind && allowed[scopeOf(id)] ? KIND[kind] : ""} />
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
