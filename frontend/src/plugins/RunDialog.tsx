import {
  createContext,
  useEffect,
  useRef,
  useState,
  useSyncExternalStore,
  type ReactNode,
} from "react";
import { usePickPath } from "../client/pickPath";
import type { LaneReaderInfo, TaskProgressInfo } from "../generated/api";
import { Dialog, DialogBody } from "../kit/Dialog";
import { Select } from "../kit/Fields";
import { Refusal } from "../kit/Refusal";
import {
  FieldControl,
  misread,
  needsValue,
  plainForm,
  settingsOf,
  textOf,
} from "../resolvers/fields";
import { useCommand } from "../session/session";
import { boundViewers, type BoundViewer } from "../viewer/bound";
import { deliverCommand, deliverWhenOpen } from "./bus";
import { viewName, type PluginCommand } from "./listing";
import { streamTask } from "./tasks";

/** What a command was run on: the item and entry of a row or lane menu, and the slot of the lane that was clicked. */
export interface RunAt {
  item?: string;
  entry?: string;
  slot?: string;
}

/** Runs a command by id as the shell does; the shell offers it so a view's key reaches the commands it may. */
export const RunCommandContext = createContext<(id: string, at?: RunAt) => void>(() => {});

const NONE = "";

const progressText = ({ done, total, text }: TaskProgressInfo) =>
  [text, total ? `${done ?? 0} / ${total}` : done].filter((part) => part !== undefined).join(" ");

/**
 * The dialog of a plugin command that runs a decoder or a task over the lanes of the viewer `target`: a lane per input
 * (the clicked `slot`, else a lane of the same name, filled in), then the options. A decoder sets its derived lane,
 * named by the command, on the item; a task streams with its progress until it is done, and Cancel stops it.
 */
export function RunDialog({
  command,
  reader,
  target,
  slot,
  onClose,
}: {
  command: PluginCommand;
  reader: LaneReaderInfo;
  target: BoundViewer;
  slot?: string;
  onClose: () => void;
}) {
  const send = useCommand();
  const { lanes } = useSyncExternalStore(target.stream.subscribe, target.stream.getSnapshot);
  const [picks, setPicks] = useState<Record<string, string>>(() =>
    Object.fromEntries(
      reader.lanes.map(([key, label], at) => [
        key,
        (at === 0 && lanes.find((found) => found.slot === slot)?.slot) ||
          lanes.find((found) => found.slot === key || found.label === label)?.slot ||
          NONE,
      ]),
    ),
  );
  const [draft, setDraft] = useState(() => textOf(reader.options, {}));
  const [progress, setProgress] = useState<TaskProgressInfo | null>(null);
  const [failure, setFailure] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const request = useRef<AbortController | null>(null);
  useEffect(() => () => request.current?.abort(), []);

  const form = plainForm(draft, setDraft, usePickPath());
  const options = settingsOf(reader.options, draft);
  const incomplete =
    Object.values(picks).includes(NONE) ||
    reader.options.some(
      (field) => misread(field, draft) || (needsValue(field) && options[field.key] === undefined),
    );

  const run = async () => {
    const controller = new AbortController();
    request.current = controller;
    setFailure(null);
    setProgress(null);
    setBusy(true);
    try {
      if (reader.kind === "decoder")
        await send({
          type: "content",
          command: {
            type: "setDerivedLane",
            item: target.item,
            slot: command.id,
            lane: {
              label: command.title,
              handler: reader.id,
              options,
              lanes: picks,
              video: reader.video,
            },
          },
        });
      else
        await streamTask(
          target,
          { handler: reader.id, lanes: picks, options },
          setProgress,
          controller.signal,
        );
      onClose();
    } catch (error) {
      if (controller.signal.aborted) return;
      setFailure(error instanceof Error ? error.message : String(error));
      setBusy(false);
    }
  };

  return (
    <Dialog
      title={command.title}
      okLabel="Run"
      busy={busy || incomplete}
      onOk={() => void run()}
      onCancel={() => {
        request.current?.abort();
        onClose();
      }}
    >
      <DialogBody>
        {reader.lanes.map(([key, label]) => (
          <Select
            key={key}
            label={label}
            value={picks[key]}
            options={[[NONE, "Choose"], ...lanes.map((lane) => [lane.slot, lane.label] as const)]}
            onChange={(picked) => setPicks((now) => ({ ...now, [key]: picked }))}
            disabled={busy}
          />
        ))}
        {reader.options.map((field) => (
          <FieldControl key={field.key} field={field} form={form} />
        ))}
        {progress && (
          <p role="status" className="m-0 text-muted">
            {progressText(progress)}
          </p>
        )}
        {failure && <Refusal reason={failure} />}
      </DialogBody>
    </Dialog>
  );
}

/** What the shell lends the runner: the viewer in front, a way to show a sidebar view, and where to say why nothing ran. */
interface Shell {
  front: number | undefined;
  reveal: (view: string) => void;
  notify: (message: string) => void;
}

/**
 * Runs a plugin command by id: a decoder or task opens its dialog over the viewer `at` names (else the one in front),
 * a view is revealed and told the command. `dialog` is the open dialog, to render; `running` is whether one is open.
 */
export function useRunPlugin(
  commands: readonly PluginCommand[],
  { front, reveal, notify }: Shell,
): { run: (id: string, at?: RunAt) => void; running: boolean; dialog: ReactNode } {
  const [open, setOpen] = useState<{
    command: PluginCommand;
    reader: LaneReaderInfo;
    target: BoundViewer;
    slot?: string;
  } | null>(null);
  const run = (id: string, at: RunAt = {}) => {
    const command = commands.find((found) => found.id === id);
    if (!command) return;
    const { reader, view } = command;
    if (view) {
      const name = viewName({ plugin: command.plugin, view });
      if (view.place === "sidebar") {
        reveal(name);
        deliverWhenOpen(name, id);
      } else if (!deliverCommand(name, id)) notify(`Turn on ${view.title} in a lane menu`);
    } else if (reader) {
      const target = boundViewers(front).find(
        (viewer) =>
          (at.item === undefined || viewer.item === at.item) &&
          (at.entry === undefined || viewer.entry === at.entry),
      );
      if (target) setOpen({ command, reader, target, slot: at.slot });
      else notify("Open it first");
    }
  };
  return {
    run,
    running: open !== null,
    dialog: open && <RunDialog {...open} onClose={() => setOpen(null)} />,
  };
}
