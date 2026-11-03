import { useEffect, useState } from "react";
import { apiFetch } from "../api";
import type { RecentWorkspace } from "../generated/api";
import { useCan, useDoc } from "../session/session";

/** The id of a menu entry or start-panel row that opens the recent workspace at the path after it. */
export const RECENT_ID = "recent:";

/** What a workspace file name ends with. */
export const SUFFIX = ".ax-devil.workspace";

/** The file's name without its folder and the workspace suffix. */
export function recentName(path: string): string {
  const name = path.split(/[/\\]/).pop() ?? path;
  return name.endsWith(SUFFIX) ? name.slice(0, -SUFFIX.length) : name;
}

const DAY_MS = 86_400_000;

const startOfDay = (ms: number): number => {
  const date = new Date(ms);
  return new Date(date.getFullYear(), date.getMonth(), date.getDate()).getTime();
};

/** When a workspace was used: `today`, `yesterday` or `3 days ago` within a week, else the date. Days are local. */
export function usedOn(usedMs: number, now = Date.now()): string {
  // Rounded, so a day of 23 or 25 hours at a clock change still counts as one.
  const days = Math.round((startOfDay(now) - startOfDay(usedMs)) / DAY_MS);
  if (days < 0 || days >= 7) return new Date(usedMs).toLocaleDateString();
  return new Intl.RelativeTimeFormat(undefined, { numeric: "auto" }).format(-days, "day");
}

/**
 * The recent workspaces, newest first, asked of the backend each time `enabled` turns on and after the workspace
 * changes. Empty for a seat that may not read the host's files.
 */
export function useRecents(enabled: boolean): readonly RecentWorkspace[] {
  const host = useCan("host");
  const workspace = useDoc()?.workspace;
  const stamp = `${workspace?.epoch}:${workspace?.modified}:${workspace?.file}`;
  const [recents, setRecents] = useState<readonly RecentWorkspace[]>([]);
  useEffect(() => {
    if (!enabled || !host) return;
    let current = true;
    apiFetch("/api/workspaces/recent")
      .then((response) => (response.ok ? (response.json() as Promise<RecentWorkspace[]>) : []))
      .then(
        (list) => current && setRecents(list),
        () => undefined,
      );
    return () => {
      current = false;
    };
  }, [enabled, host, stamp]);
  return recents;
}
