import { scopeOf } from "../client/commands";
import type {
  DerivedLaneWire,
  LaneReaderInfo,
  ReaderKind,
  TaskProgressInfo,
} from "../generated/api";
import { viewName, type PluginView } from "./listing";

export interface BusLane {
  slot: string;
  label: string;
  basis: string | null;
  complete: boolean;
}

/**
 * What a view is bound to: the viewer, with the item and entry it plays. Each is null while no viewer is bound. The bus
 * numbers the bindings: when the viewer, item or entry changes, a call made under the old number is refused.
 */
export interface Binding {
  viewer: number | null;
  item: string | null;
  entry: string | null;
  lanes: readonly BusLane[];
  selection: { slot: string; entity: string } | null;
  theme: "light" | "dark";
  /** The video rectangle the view covers, in CSS pixels of the page; absent for a sidebar view. */
  video?: { x: number; y: number; width: number; height: number };
}

export interface Playhead {
  frame: number;
  timeUs: number | null;
  playing: boolean;
}

export interface ViewerSet {
  frame?: number;
  playing?: boolean;
  select?: { slot: string; entity: string } | null;
  capture?: boolean;
}

export interface TaskRun {
  handler: string;
  lanes: Record<string, string>;
  options: Record<string, unknown>;
}

/** What fulfils a call: the page's own routes and commands. The bus checks every call before it reaches one. */
export interface BusHost {
  /** Changes the bound viewer; false when it no longer shows `binding`'s item and entry. */
  viewerSet(binding: Binding, set: ViewerSet): boolean;
  /** The lane file text of up to `count` samples of the lane `slot` from sample `from`. */
  readScenes(
    binding: Binding,
    slot: string,
    from: number,
    count: number,
    signal: AbortSignal,
  ): Promise<string>;
  /** Sets (`null` removes) the derived lane `slot` of the item. */
  setLane(binding: Binding, slot: string, lane: DerivedLaneWire | null): Promise<void>;
  /** Runs a task over the entry's lanes until it is done, failed or aborted. */
  runTask(
    binding: Binding,
    run: TaskRun,
    onProgress: (progress: TaskProgressInfo) => void,
    signal: AbortSignal,
  ): Promise<void>;
  /**
   * Runs the command the pressed `key` is bound to, if `allowed` accepts its id, for the viewer `binding` names:
   * a command that cannot reach that viewer is not run.
   */
  runKey(binding: Binding, key: string, allowed: (command: string) => boolean): void;
}

/** The most a `lanes.set` carries in options: they are kept in the workspace. */
export const MOST_LANE_OPTIONS_BYTES = 256 << 10;
/** The most a `task.run` carries in options. */
export const MOST_TASK_OPTIONS_BYTES = 8 << 20;
/** How often a changing playhead is told to a view, at most. */
const PLAYHEAD_MS = 33;

/** Why a call was refused; the view reads it as `error`. */
export type Refusal =
  | "stale"
  | "no_viewer"
  | "refused"
  | "too_large"
  | "invalid"
  | "unknown_slot"
  | "unknown_call"
  | "cancelled"
  | "failed";

class Refused extends Error {
  constructor(
    readonly code: Refusal,
    reason: string,
  ) {
    super(reason);
  }
}

const refuse = (code: Refusal, reason: string): never => {
  throw new Refused(code, reason);
};

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value);

const text = (value: unknown, name: string): string =>
  typeof value === "string" && value !== "" ? value : refuse("invalid", `${name} is text`);

const count = (value: unknown, name: string): number =>
  Number.isInteger(value) && (value as number) >= 0
    ? (value as number)
    : refuse("invalid", `${name} is a whole number`);

const sizeOf = (value: unknown): number => new TextEncoder().encode(JSON.stringify(value)).length;

/** The call's options, as an object, that no option of a `file` or `folder` kind is among, and at most `most` bytes. */
function optionsOf(value: unknown, reader: LaneReaderInfo, most: number): Record<string, unknown> {
  const options = value === undefined ? {} : value;
  if (!isRecord(options)) return refuse("invalid", "options is an object");
  const paths = reader.options.filter(({ kind }) => kind.kind === "File" || kind.kind === "Folder");
  if (paths.some(({ key }) => key in options))
    refuse("refused", "A path option cannot be given by a view");
  if (sizeOf(options) > most) refuse("too_large", "options is too large");
  return options;
}

function lanesOf(value: unknown): Record<string, string> {
  if (!isRecord(value)) return refuse("invalid", "lanes is an object");
  const lanes: Record<string, string> = {};
  for (const [key, slot] of Object.entries(value)) lanes[key] = text(slot, "a slot");
  return lanes;
}

const whenAborted = (signal: AbortSignal): Promise<never> =>
  new Promise((_done, fail) =>
    signal.addEventListener("abort", () => fail(new Error("cancelled")), { once: true }),
  );

/** A way a view speaks to the host over its port, until it is closed. */
export interface Bus {
  port: MessagePort;
  /** Binds the view to `binding`, or tells it of a change in what it is bound to; a new viewer, item or entry is a new epoch. */
  rebind(binding: Binding): void;
  /** Tells the view where the bound viewer is; only the latest is kept while one waits. */
  playhead(playhead: Playhead): void;
  command(id: string): void;
  close(): void;
}

/**
 * The bus of one view over one `MessageChannel`. The view sends `{type: "call", id, call, epoch, args}` and gets
 * `{type: "reply", id, ok, value | error, reason}`; it sends `{type: "key", key, epoch}` for a key it did not handle. The
 * host sends `context`, `playhead`, `command` and (for a running task) `progress` events, each `{type, ...}`.
 * A call whose epoch is not the one of the last `context` is answered `stale` and reaches nothing; a `key` of another
 * epoch is dropped, as it has no reply. The one table of calls is `calls` below.
 */
export function createBus(plugin: PluginView, host: BusHost): Bus {
  const channel = new MessageChannel();
  const name = viewName(plugin);
  const hostPort = channel.port1;
  const ownCommands = new Set(plugin.commands.map(({ id }) => id));
  const running = new Map<number, AbortController>();
  /** The slots of the lanes this view set, which its later lanes may read before the viewer lists them. */
  const derived = new Set<string>();
  let bound: Binding | null = null;
  let epoch = 0;
  let closed = false;
  let waiting: Playhead | null = null;
  let told = 0;
  let timer: ReturnType<typeof setTimeout> | undefined;

  const send = (message: Record<string, unknown>) => {
    if (!closed) hostPort.postMessage(message);
  };
  const reader = (id: unknown, kind: ReaderKind): LaneReaderInfo => {
    const found = plugin.readers.find((candidate) => candidate.id === id);
    return found?.kind === kind
      ? found
      : refuse("refused", `This plugin has no ${kind} ${String(id)}`);
  };
  /** The binding a call acts on: the one the call's epoch named, with a viewer. */
  const viewer = (): Binding & { item: string; entry: string } => {
    if (bound?.item == null || bound.entry == null) return refuse("no_viewer", "No viewer");
    return bound as Binding & { item: string; entry: string };
  };
  /** Slots of this plugin's lanes are `<plugin>.<name>`, so a plugin cannot reach a lane of the entry or of another plugin. */
  const ownSlot = (value: unknown): string => {
    const name = text(value, "slot");
    return name.startsWith(`${plugin.plugin}.`)
      ? name
      : refuse("refused", "Not a slot of this plugin");
  };

  const calls: Record<
    string,
    (args: Record<string, unknown>, id: number, signal: AbortSignal) => unknown
  > = {
    "viewer.set": (args) => {
      const now = viewer();
      const { frame, playing, select, capture } = args;
      const set: ViewerSet = {};
      if (frame !== undefined) set.frame = count(frame, "frame");
      if (playing !== undefined) set.playing = playing === true;
      if (capture !== undefined) set.capture = capture === true;
      if (select !== undefined)
        set.select =
          select === null
            ? null
            : isRecord(select)
              ? {
                  slot: text(select.slot, "select.slot"),
                  entity: text(select.entity, "select.entity"),
                }
              : refuse("invalid", "select is an object or null");
      if (!host.viewerSet(now, set)) refuse("no_viewer", "No viewer");
      return undefined;
    },
    "scenes.read": (args, _id, signal) => {
      const now = viewer();
      return host.readScenes(
        now,
        text(args.slot, "slot"),
        count(args.from, "from"),
        count(args.count, "count"),
        signal,
      );
    },
    "lanes.set": async (args) => {
      const now = viewer();
      const target = ownSlot(args.slot);
      if (args.remove === true) {
        await host.setLane(now, target, null);
        derived.delete(target);
        return undefined;
      }
      const decoder = reader(args.handler, "decoder");
      const lanes = lanesOf(args.lanes);
      const known = new Set([...now.lanes.map(({ slot }) => slot), ...derived]);
      const unknown = Object.values(lanes).find((slot) => !known.has(slot));
      if (unknown !== undefined) refuse("unknown_slot", `No lane ${unknown} to read`);
      await host.setLane(now, target, {
        label: text(args.label, "label"),
        handler: decoder.id,
        options: optionsOf(args.options, decoder, MOST_LANE_OPTIONS_BYTES),
        lanes,
        video: decoder.video,
      });
      derived.add(target);
      return undefined;
    },
    "task.run": async (args, id, signal) => {
      const now = viewer();
      const task = reader(args.handler, "task");
      const run: TaskRun = {
        handler: task.id,
        lanes: lanesOf(args.lanes),
        options: optionsOf(args.options, task, MOST_TASK_OPTIONS_BYTES),
      };
      await host.runTask(
        now,
        run,
        (progress) => send({ type: "progress", id, ...progress }),
        signal,
      );
      return undefined;
    },
  };

  const answer = async (call: Record<string, unknown>) => {
    const id = call.id;
    if (typeof id !== "number") return;
    const reply = (fields: Record<string, unknown>) => send({ type: "reply", id, ...fields });
    const controller = new AbortController();
    try {
      // A cancel only ends a call of this view, so it lands nothing on a later binding and needs no epoch.
      if (call.call === "cancel") {
        const args = isRecord(call.args) ? call.args : {};
        running.get(count(args.id, "id"))?.abort();
        return reply({ ok: true });
      }
      if (bound === null || call.epoch !== epoch)
        refuse("stale", "The view is bound to something else");
      const handle = typeof call.call === "string" ? calls[call.call] : undefined;
      if (!handle) return refuse("unknown_call", `No call ${String(call.call)}`);
      const args = call.args === undefined ? {} : call.args;
      if (!isRecord(args)) return refuse("invalid", "args is an object");
      running.set(id, controller);
      // A call that cannot be interrupted itself still ends at its cancel; a change already sent is not undone.
      const value = await Promise.race([
        handle(args, id, controller.signal),
        whenAborted(controller.signal),
      ]);
      reply({ ok: true, value });
    } catch (error) {
      if (error instanceof Refused) reply({ ok: false, error: error.code, reason: error.message });
      else if (controller.signal.aborted) reply({ ok: false, error: "cancelled" });
      else
        reply({
          ok: false,
          error: "failed",
          reason: error instanceof Error ? error.message : String(error),
        });
    } finally {
      running.delete(id);
    }
  };

  hostPort.onmessage = ({ data }: MessageEvent<unknown>) => {
    if (!isRecord(data)) return;
    if (data.type === "call") void answer(data);
    else if (
      data.type === "key" &&
      typeof data.key === "string" &&
      bound !== null &&
      epoch === data.epoch
    )
      host.runKey(bound, data.key, (id) => ownCommands.has(id) || scopeOf(id) === "view");
  };

  const flush = () => {
    timer = undefined;
    if (!waiting) return;
    const { frame, timeUs, playing } = waiting;
    waiting = null;
    told = Date.now();
    send({ type: "playhead", frame, time: timeUs, playing });
  };

  const bus: Bus = {
    port: channel.port2,
    rebind(binding) {
      if (
        bound?.viewer !== binding.viewer ||
        bound.item !== binding.item ||
        bound.entry !== binding.entry
      ) {
        epoch += 1;
        derived.clear();
      }
      bound = binding;
      const { item, entry, lanes, selection, theme, video } = binding;
      send({ type: "context", epoch, item, entry, lanes, selection, theme, video });
      // A command run while the view was still opening reaches it once it has its context.
      for (const id of queued.get(name) ?? []) bus.command(id);
      queued.delete(name);
    },
    playhead(playhead) {
      waiting = playhead;
      if (timer === undefined)
        timer = setTimeout(flush, Math.max(0, told + PLAYHEAD_MS - Date.now()));
    },
    command: (id) => send({ type: "command", id }),
    close() {
      closed = true;
      clearTimeout(timer);
      running.forEach((controller) => controller.abort());
      hostPort.close();
      alive.get(name)?.delete(bus);
    },
  };
  const open = alive.get(name) ?? new Set<Bus>();
  open.add(bus);
  alive.set(name, open);
  return bus;
}

/** The open buses by view name, `<plugin id>.<view id>`. */
const alive = new Map<string, Set<Bus>>();
/** Commands run on a view that is not open yet, by view name, until its bus has its first context. */
const queued = new Map<string, string[]>();

/** Tells the command `commandId` to the open views named `viewId`; whether any was open. */
export function deliverCommand(viewId: string, commandId: string): boolean {
  const buses = [...(alive.get(viewId) ?? [])];
  buses.forEach((bus) => bus.command(commandId));
  return buses.length > 0;
}

/** Tells the command now if the view is open, else when it opens: for a view the command has just revealed. */
export function deliverWhenOpen(viewId: string, commandId: string): void {
  if (!deliverCommand(viewId, commandId)) queued.set(viewId, [commandId]);
}

/**
 * The `load` handler of a view's frame. The first load makes the bus and gives the frame its port; a later one means
 * the view left its page, which ends the bus and calls `onLeft`: whatever page is there now is not the view.
 */
export function onFrameLoad(
  make: () => Bus,
  give: (port: MessagePort) => void,
  onLeft: () => void,
): () => void {
  let bus: Bus | null = null;
  return () => {
    if (bus) {
      bus.close();
      onLeft();
      return;
    }
    bus = make();
    give(bus.port);
  };
}
