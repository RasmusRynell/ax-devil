import { useEffect } from "react";
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
  const [open] = OPEN;
  const canOpen = allowed[scopeOf(open[0])];
  // Open Workspace is the default button: Enter runs it when nothing else has the focus (a focused button keeps
  // its own Enter), unless a dialog is open or a shortcut already took the key.
  useEffect(() => {
    if (!canOpen) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (
        event.key !== "Enter" ||
        event.target !== document.body ||
        event.defaultPrevented ||
        event.ctrlKey ||
        event.altKey ||
        event.metaKey ||
        document.querySelector("dialog[open]")
      )
        return;
      event.preventDefault();
      onRun(open[0]);
    };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [canOpen, onRun, open]);
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
