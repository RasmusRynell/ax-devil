import { useEffect, useRef, useState, useSyncExternalStore, type ReactNode } from "react";
import { usePickPath } from "../client/pickPath";
import type { OperationInfo, TaskProgressInfo } from "../generated/api";
import { Dialog, DialogBody } from "../kit/Dialog";
import { Select } from "../kit/Fields";
import { Refusal } from "../kit/Refusal";
import { FieldControl, misread, needsValue, type FieldForm } from "../resolvers/fields";
import { useCommand, useSession } from "../session/session";
import { deliverCommand, deliverWhenOpen } from "./bus";
import { settingsOf, textOf } from "./PluginsView";
import {
  boundViewers,
  streamTask,
  type BoundViewer,
  type PluginCommand,
  type RunAt,
} from "./usePlugins";

const NONE = "";

const progressText = ({ done, total, text }: TaskProgressInfo) =>
  [text, total ? `${done ?? 0} / ${total}` : done].filter((part) => part !== undefined).join(" ");

/**
 * The dialog of a plugin command that runs a decoder or a task over the lanes of the viewer `target`: a lane per input
 * (the clicked `lane`, else a lane of the same name, filled in), then the options. A decoder sets its derived lane,
 * named by the command, on the item; a task streams with its progress until it is done, and Cancel stops it.
 */
export function RunDialog({
  command,
  operation,
  target,
  lane,
  onClose,
}: {
  command: PluginCommand;
  operation: OperationInfo;
  target: BoundViewer;
  lane?: string;
  onClose: () => void;
}) {
  const session = useSession().session?.id ?? "";
  const send = useCommand();
  const pickPath = usePickPath();
  const { lanes } = useSyncExternalStore(target.stream.subscribe, target.stream.getSnapshot);
  const [picks, setPicks] = useState<Record<string, string>>(() =>
    Object.fromEntries(
      operation.lanes.map(([key, label], at) => [
        key,
        (at === 0 && lanes.find(({ slot }) => slot === lane)?.slot) ||
          lanes.find((found) => found.slot === key || found.label === label)?.slot ||
          NONE,
      ]),
    ),
  );
  const [draft, setDraft] = useState(() => textOf(operation.options, {}));
  const [progress, setProgress] = useState<TaskProgressInfo | null>(null);
  const [failure, setFailure] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const request = useRef<AbortController | null>(null);
  useEffect(() => () => request.current?.abort(), []);

  const form: FieldForm = {
    decoders: [],
    values: draft,
    scans: {},
    pickPath,
    set: (key, value) => setDraft((current) => ({ ...current, [key]: value })),
    canScan: () => false,
    scan: () => undefined,
    toggle: () => undefined,
    toggleAll: () => undefined,
  };
  const options = settingsOf(operation.options, draft);
  const incomplete =
    Object.values(picks).includes(NONE) ||
    operation.options.some(
      (field) => misread(field, draft) || (needsValue(field) && options[field.key] === undefined),
    );

  const run = async () => {
    const controller = new AbortController();
    request.current = controller;
    setFailure(null);
    setProgress(null);
    setBusy(true);
    try {
      if (operation.kind === "decoder")
        await send({
          type: "content",
          command: {
            type: "setDerivedLane",
            item: target.item,
            slot: command.id,
            lane: {
              label: command.title,
              handler: operation.id,
              options,
              lanes: picks,
              video: operation.video,
            },
          },
        });
      else
        await streamTask(
          session,
          target,
          { runs: operation.id, lanes: picks, video: operation.video, options },
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
        {operation.lanes.map(([key, label]) => (
          <Select
            key={key}
            label={label}
            value={picks[key]}
            options={[
              [NONE, "Choose"],
              ...lanes.map(({ slot, label: name }) => [slot, name] as const),
            ]}
            onChange={(slot) => setPicks((now) => ({ ...now, [key]: slot }))}
            disabled={busy}
          />
        ))}
        {operation.options.map((field) => (
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
    operation: OperationInfo;
    target: BoundViewer;
    lane?: string;
  } | null>(null);
  const run = (id: string, at: RunAt = {}) => {
    const command = commands.find((found) => found.id === id);
    if (!command) return;
    const { operation, view } = command;
    if (view) {
      const name = `${command.plugin}.${view.id}`;
      if (view.place === "sidebar") {
        reveal(name);
        deliverWhenOpen(name, id);
      } else if (!deliverCommand(name, id)) notify(`Turn on ${view.title} in a lane menu`);
    } else if (operation) {
      const target = boundViewers(front).find(
        (viewer) =>
          (at.item === undefined || viewer.item === at.item) &&
          (at.entry === undefined || viewer.entry === at.entry),
      );
      if (target) setOpen({ command, operation, target, lane: at.lane });
      else notify("Open it first");
    }
  };
  return {
    run,
    running: open !== null,
    dialog: open && <RunDialog {...open} onClose={() => setOpen(null)} />,
  };
}
