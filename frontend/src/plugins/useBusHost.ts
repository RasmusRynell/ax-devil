import { useContext, useMemo, useRef } from "react";
import { apiFetch, failWith } from "../client/api";
import { isPluginCommand } from "../client/commands";
import { useSettings } from "../client/settings";
import { useCommand } from "../session/session";
import type { BoundViewer } from "../viewer/bound";
import { useViewers } from "../viewer/useViewers";
import type { BusHost } from "./bus";
import { RunCommandContext } from "./RunDialog";
import { streamTask } from "./tasks";

/**
 * What fulfils a view's calls: the bound viewer's own actions and the routes the page already uses. The host
 * stays the same object while `target`, `capturing` and `onCapture` change, so a bus made once always acts on the
 * current ones.
 */
export function useBusHost(
  target: BoundViewer | undefined,
  capturing: boolean,
  onCapture: (capture: boolean) => void,
): BusHost {
  const command = useCommand();
  const { shortcuts } = useSettings();
  const run = useContext(RunCommandContext);
  const { active } = useViewers();
  const latest = useRef({ target, capturing, onCapture });
  latest.current = { target, capturing, onCapture };
  const used = useRef({ command, shortcuts, run, front: active?.id });
  used.current = { command, shortcuts, run, front: active?.id };
  return useMemo<BusHost>(
    () => ({
      viewerSet(binding, set) {
        const { target: now, onCapture: capture } = latest.current;
        if (!now || now.item !== binding.item || now.entry !== binding.entry) return false;
        const { stream } = now;
        if (set.frame !== undefined) stream.seek(set.frame);
        if (set.playing === true) stream.play();
        else if (set.playing === false) stream.pause();
        if (set.select === null) stream.select(null);
        else if (set.select) {
          const lanes = stream.getSnapshot().lanes;
          const lane = lanes.findIndex(({ slot }) => slot === set.select?.slot);
          const source = lanes[lane]?.source;
          if (source == null) throw new Error("No such lane");
          stream.select({ lane, source, entity: set.select.entity });
        }
        if (set.capture !== undefined) capture(set.capture);
        return true;
      },
      async readScenes({ item, entry }, slot, from, count, signal) {
        const query = new URLSearchParams({
          item: item ?? "",
          entry: entry ?? "",
          slot,
          from: String(from),
          count: String(count),
        });
        const response = await apiFetch(`/api/lanes/scenes?${query}`, { signal });
        return response.ok ? response.text() : failWith(response);
      },
      setLane({ item }, slot, lane) {
        return used.current.command({
          type: "content",
          command: { type: "setDerivedLane", item: item ?? "", slot, lane },
        });
      },
      runTask: streamTask,
      runKey(binding, key, allowed) {
        const { target: now, capturing: held, onCapture: capture } = latest.current;
        if (!now || now.item !== binding.item || now.entry !== binding.entry) return;
        if (key === "Escape" && held) return capture(false);
        const { shortcuts: keys, run: runCommand, front } = used.current;
        const found = Object.keys(keys).find((id) => keys[id] === key && allowed(id));
        if (!found) return;
        // A plugin command is run on the viewer the view is bound to. A built-in one is the front viewer's, so a
        // key from a view bound to another viewer runs nothing rather than act on the front one.
        if (isPluginCommand(found))
          runCommand(found, { item: binding.item ?? undefined, entry: binding.entry ?? undefined });
        else if (now.viewer === front) runCommand(found);
      },
    }),
    [],
  );
}
