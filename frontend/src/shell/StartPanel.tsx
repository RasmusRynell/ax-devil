import { command } from "../commands";
import { Button } from "../kit/Button";
import type { IconName } from "../kit/icons";

const OPEN: readonly [id: string, icon: IconName][] = [
  ["open-workspace", "folder"],
  ["add-video", "square-play"],
  ["add-live-stream", "video"],
  ["add-playlist", "list-video"],
];

/** The Workspace view's start panel: the ways to open content, and the recent workspaces. */
export function StartPanel({ onRun }: { onRun: (id: string) => void }) {
  return (
    <div className="flex flex-col gap-l">
      <div className="grid max-sm:grid-cols-2">
        {OPEN.map(([id, icon]) => (
          <Button key={id} icon={icon} className="justify-start" onClick={() => onRun(id)}>
            {command(id).label}
          </Button>
        ))}
      </div>
      <section className="flex flex-col gap-xs">
        <h3 className="px-m text-small font-semibold text-muted uppercase">Recent workspaces</h3>
        <p className="m-0 px-m text-muted">No recent workspaces</p>
      </section>
    </div>
  );
}
