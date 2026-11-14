import {
  createContext,
  createElement,
  useContext,
  useMemo,
  useRef,
  useSyncExternalStore,
} from "react";
import type { ReactNode } from "react";
import { apiFetch } from "../client/api";
import { ask } from "../client/ask";
import { useAccess } from "../client/access";
import { reasonOf } from "../client/errors";
import { useResource } from "../client/resource";
import { useSettings } from "../client/settings";
import { COMMANDS } from "../generated/settings";
import type { ErrorWire, PluginsInfo, TaskLine } from "../generated/api";
import type { PlaybackStream } from "../playback/stream";
import { useCommand, useSession } from "../session/session";
import type { BusHost, PluginView } from "./bus";

/** What a plugin view is called across the app: `<plugin id>.<view id>`, which is also its `SidebarView::Plugin` id. */
export const viewName = ({ plugin, view }: PluginView): string => `${plugin}.${view.id}`;

/** The views of the plugins that loaded, with the plugin's own operations and commands. */
const viewsOf = ({ plugins }: PluginsInfo): PluginView[] =>
  plugins.flatMap(({ status }) =>
    status.state === "loaded"
      ? status.views.map((view) => ({
          plugin: status.id,
          view,
          operations: status.operations,
          commands: status.commands,
        }))
      : [],
  );

/** The listing's views; a listing that does not read is a failed read, which is tried again. */
async function readViews(path: string, signal: AbortSignal): Promise<PluginView[]> {
  const answer = await ask<PluginsInfo>(path, { signal });
  if (!answer.ok) throw answer.error;
  // An answer that is no listing has no views; the Plugins view is where it is reported.
  return Array.isArray(answer.value.plugins) ? viewsOf(answer.value) : [];
}

const NO_VIEWS: readonly PluginView[] = [];
const ViewsContext = createContext<readonly PluginView[]>(NO_VIEWS);

/**
 * Lists the plugin views for what is below it. Only an Owner reads the listing, so a Guest's window has none; a page
 * without this provider has none either.
 */
export function PluginViewsProvider({ children }: { children: ReactNode }) {
  const { owner } = useAccess();
  const { value } = useResource<PluginView[]>("plugins", owner ? "/api/plugins" : null, {
    read: readViews,
  });
  return createElement(ViewsContext, { value: value ?? NO_VIEWS }, children);
}

/** The plugin views of `place`. */
export function usePluginViews(place: "sidebar" | "video"): readonly PluginView[] {
  const views = useContext(ViewsContext);
  return useMemo(() => views.filter(({ view }) => view.place === place), [views, place]);
}

/** Runs a command by id as the shell does; the shell offers it so a view's key reaches the commands it may. */
export const RunCommandContext = createContext<(id: string) => void>(() => {});

/** A viewer that plays an entry and can be driven. */
export interface BoundViewer {
  viewer: number;
  item: string;
  entry: string;
  stream: PlaybackStream;
}

const bound = new Map<number, BoundViewer>();
let held: ReadonlyMap<number, BoundViewer> = new Map();
const listeners = new Set<() => void>();

/** Offers `target` to the sidebar views while the viewer plays; the returned function takes it back. */
export function bindViewer(target: BoundViewer): () => void {
  const change = (next: () => void) => {
    next();
    held = new Map(bound);
    listeners.forEach((listener) => listener());
  };
  change(() => bound.set(target.viewer, target));
  return () => change(() => bound.get(target.viewer) === target && bound.delete(target.viewer));
}

/** The viewer `viewer` offered, once it plays an entry. */
export function useBoundViewer(viewer: number | undefined): BoundViewer | undefined {
  const all = useSyncExternalStore(
    (listener) => (listeners.add(listener), () => void listeners.delete(listener)),
    () => held,
  );
  return viewer === undefined ? undefined : all.get(viewer);
}

/** Whatever of a view's host that changes between renders. */
interface Latest {
  target: BoundViewer | undefined;
  capturing: boolean;
  onCapture: (capture: boolean) => void;
}

const fail = async (response: Response): Promise<never> => {
  const error = (await response.json().catch(() => null)) as ErrorWire | null;
  throw new Error(error?.code ? reasonOf(error) : `The backend answered ${response.status}`);
};

/** The lines of a streamed answer, as they arrive. */
async function* linesOf(response: Response): AsyncGenerator<string> {
  const reader = response.body?.getReader();
  if (!reader) return;
  const decoder = new TextDecoder();
  let pending = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    const parts = (pending + decoder.decode(value, { stream: true })).split("\n");
    pending = parts.pop() ?? "";
    yield* parts.filter(Boolean);
  }
  if (pending) yield pending;
}

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
  const session = useSession().session?.id ?? "";
  const command = useCommand();
  const { shortcuts } = useSettings();
  const run = useContext(RunCommandContext);
  const latest = useRef<Latest>({ target, capturing, onCapture });
  latest.current = { target, capturing, onCapture };
  const used = useRef({ session, command, shortcuts, run });
  used.current = { session, command, shortcuts, run };
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
          const lane = lanes.findIndex(({ slot }) => slot === set.select?.lane);
          const source = lanes[lane]?.source;
          if (source == null) throw new Error("No such lane");
          stream.select({ lane, source, entity: set.select.entity });
        }
        if (set.capture !== undefined) capture(set.capture);
        return true;
      },
      async readScenes({ item, entry }, slot, from, count, signal) {
        const query = new URLSearchParams({
          session: used.current.session,
          item: item ?? "",
          entry: entry ?? "",
          slot,
          from: String(from),
          count: String(count),
        });
        const response = await apiFetch(`/api/lanes/scenes?${query}`, { signal });
        return response.ok ? response.text() : fail(response);
      },
      setLane({ item }, slot, lane) {
        return used.current.command({
          type: "content",
          command: { type: "setDerivedLane", item: item ?? "", slot, lane },
        });
      },
      async runTask({ item, entry }, { runs, lanes, video, options }, onProgress, signal) {
        const response = await apiFetch(`/api/tasks/${encodeURIComponent(runs)}/run`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            session: used.current.session,
            item,
            entry,
            lanes,
            video,
            options,
          }),
          signal,
        });
        if (!response.ok) return fail(response);
        for await (const line of linesOf(response)) {
          const message = JSON.parse(line) as TaskLine;
          if ("progress" in message) onProgress(message.progress);
          else if ("error" in message) throw new Error(reasonOf(message.error));
          else return;
        }
        throw new Error("The task ended without saying it was done");
      },
      runKey(key, allowed) {
        if (key === "Escape" && latest.current.capturing) return latest.current.onCapture(false);
        const { shortcuts: keys, run: runCommand } = used.current;
        const found = COMMANDS.find(({ id }) => keys[id] === key && allowed(id));
        if (found) runCommand(found.id);
      },
    }),
    [],
  );
}

/** The video views a lane can show over its picture: which are on for it, and a way to turn one on or off. */
export interface LaneViews {
  views: readonly PluginView[];
  shown: ReadonlySet<string>;
  toggle: (view: string) => void;
}

/** What the lane menu offers of video views for the lane it is open for; none outside a viewer that offers them. */
export const LaneViewsContext = createContext<LaneViews | null>(null);
