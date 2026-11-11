import type { RecentWorkspace } from "./generated/api";
import { useResource } from "./resource";
import { useCan } from "./session/session";

/** The id of a menu entry or start-panel row that opens the recent workspace at the path after it. */
export const RECENT_ID = "recent:";

/** What a workspace file name ends with. */
export const SUFFIX = ".ax-devil.workspace";

/** A workspace file name without the workspace suffix. */
export function recentLabel(name: string): string {
  return name.endsWith(SUFFIX) ? name.slice(0, -SUFFIX.length) : name;
}

const DAY_MS = 86_400_000;

const startOfDay = (ms: number): number => {
  const date = new Date(ms);
  return new Date(date.getFullYear(), date.getMonth(), date.getDate()).getTime();
};

/**
 * When a workspace was used: `today` (a time ahead of the clock too), the weekday within the last six days, else the
 * month and day, with the year when it is not this year's. Days are local.
 */
export function usedOn(usedMs: number, now = Date.now()): string {
  // Rounded, so a day of 23 or 25 hours at a clock change still counts as one.
  const days = Math.round((startOfDay(now) - startOfDay(usedMs)) / DAY_MS);
  if (days <= 0) return "today";
  const used = new Date(usedMs);
  if (days <= 6) return used.toLocaleDateString(undefined, { weekday: "short" });
  const thisYear = used.getFullYear() === new Date(now).getFullYear();
  return used.toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
    ...(thisYear ? {} : { year: "numeric" }),
  });
}

/** The folder a workspace file is in; a filesystem root (`/`, `C:\`) keeps its separator. */
export function folderOf(path: string): string {
  const cut = Math.max(path.lastIndexOf("/"), path.lastIndexOf("\\"));
  const folder = path.slice(0, cut);
  return cut < 0
    ? ""
    : folder === "" || /^[A-Za-z]:$/.test(folder)
      ? path.slice(0, cut + 1)
      : folder;
}

/**
 * The recent workspaces, newest first, asked of the backend each time `enabled` turns on and after each change it
 * announces. Empty for a seat that may not read the host's files.
 */
export function useRecents(enabled: boolean): readonly RecentWorkspace[] {
  const host = useCan("host");
  const { value } = useResource<RecentWorkspace[]>(
    "recents",
    enabled && host ? "/api/workspaces/recent" : null,
  );
  return value ?? [];
}
