import { useEffect, useSyncExternalStore } from "react";
import type { PluginsInfo } from "../generated/api";
import { ask } from "../resolvers/handlers";

let failed = 0;
let asked = false;
const listeners = new Set<() => void>();

/** How many of `info`'s plugins did not load. */
export const failedIn = ({ plugins }: PluginsInfo): number =>
  plugins.filter(({ status }) => status.state === "failed").length;

/** Reports how many plugins failed to load, as the Plugins view last listed them. */
export function reportFailedPlugins(count: number): void {
  asked = true;
  if (count === failed) return;
  failed = count;
  listeners.forEach((listener) => listener());
}

/**
 * How many plugins failed to load, for the activity bar's warning on Plugins: asked once when the window opens, and
 * again whenever the Plugins view lists them.
 */
export function useFailedPlugins(): number {
  useEffect(() => {
    if (asked) return;
    asked = true;
    ask<PluginsInfo>("/api/plugins", {})
      .then((answer) => {
        if (answer.ok) reportFailedPlugins(failedIn(answer.value));
      })
      // The warning is a courtesy: an answer that does not read is no reason to disturb the window.
      .catch(() => undefined);
  }, []);
  return useSyncExternalStore(
    (listener) => {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    () => failed,
  );
}
