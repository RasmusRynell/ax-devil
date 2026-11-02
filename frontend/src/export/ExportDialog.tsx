import { useRef, useState, useSyncExternalStore } from "react";
import { createPortal } from "react-dom";
import type { ExportFormatWire, ExportQualityWire } from "../generated/api";
import { Dialog } from "../kit/Dialog";
import { Checkbox, Select, TextField } from "../kit/Fields";
import type { PlaybackView } from "../playback/view";
import { useServerKind } from "../shell/windowBridge";
import { choicesOf } from "./lanes";
import type { ExportJobs, ExportStatus } from "./jobs";

type Kind = "mp4" | "png";

const KINDS: readonly (readonly [Kind, string])[] = [
  ["mp4", "Video MP4"],
  ["png", "This frame PNG"],
];

const QUALITIES: readonly (readonly [ExportQualityWire, string])[] = [
  ["lossless", "Lossless"],
  ["high", "High"],
  ["balanced", "Balanced"],
  ["small", "Small"],
];

/** A whole number from its text; `null` for anything else. */
const wholeNumber = (text: string): number | null =>
  /^\d+$/.test(text.trim()) ? Number(text) : null;

/** The text of an export that finished or could not start: shown in place of the form. */
function Outcome({ status }: { status: Exclude<ExportStatus, { phase: "idle" | "running" }> }) {
  if (status.phase === "failed")
    return (
      <div role="alert" className="flex flex-col gap-s">
        <p className="m-0 break-words">{status.reason}</p>
        {status.location && (
          <p className="m-0 text-small break-all text-muted">{status.location}</p>
        )}
      </div>
    );
  return (
    <p role="status" className="m-0 break-all">
      {status.phase === "done" ? `Saved ${status.path}` : "Export cancelled"}
    </p>
  );
}

/** The running export's bar and count. */
function Progress({ done, total }: { done: number; total: number }) {
  return (
    <div role="status" className="flex flex-col gap-s">
      <div
        role="progressbar"
        aria-label="Export progress"
        aria-valuemin={0}
        aria-valuemax={total}
        aria-valuenow={done}
        className="h-s w-full overflow-hidden rounded-control bg-hover"
      >
        <div
          className="h-full bg-accent"
          style={{ width: `${total > 0 ? (done / total) * 100 : 0}%` }}
        />
      </div>
      <span className="tabular-nums">
        {done} / {total}
      </span>
    </div>
  );
}

/**
 * The modal export of the viewer's video: which lanes, a video or this frame, the frames and the quality, then the
 * running job with Cancel. The backend writes the file; a local window picks it with the native save dialog and any
 * other types a path on the server. `view` is read again when the job starts, so it carries the lanes as they are then.
 */
export function ExportDialog({
  jobs,
  view,
  name,
  onClose,
}: {
  jobs: ExportJobs;
  view: PlaybackView;
  name: string;
  onClose: () => void;
}) {
  const status = useSyncExternalStore(jobs.subscribe, jobs.getSnapshot);
  const latest = useRef(view);
  latest.current = view;
  const bridge = useServerKind() === "local" ? window.axDevilWindow : undefined;
  const choices = choicesOf(view.lanes);
  const [unchecked, setUnchecked] = useState<ReadonlySet<number>>(new Set());
  const [kind, setKind] = useState<Kind>("mp4");
  const [quality, setQuality] = useState<ExportQualityWire>("balanced");
  const [from, setFrom] = useState("0");
  const [to, setTo] = useState(String(view.frameCount - 1));
  const [path, setPath] = useState("");
  const [failure, setFailure] = useState<string | null>(null);
  const [choosing, setChoosing] = useState(false);

  const picked = choices.filter((choice) => choice.wire && !unchecked.has(choice.lane));
  const first = kind === "png" ? view.playhead : wholeNumber(from);
  const last = kind === "png" ? view.playhead : wholeNumber(to);
  const framesOk = first !== null && last !== null && first <= last && last < view.frameCount;
  const ready = picked.length > 0 && framesOk && (bridge !== undefined || path.trim() !== "");

  const start = async () => {
    if (first === null || last === null) return;
    setFailure(null);
    setChoosing(true);
    try {
      const stem = name.replace(/\.[^.]*$/, "") || name;
      const target = bridge ? await bridge.pickExportFile(`${stem}.${kind}`, kind) : path.trim();
      if (!target) return;
      // The lanes are read now: the picker may have been open while they changed.
      const lanes = choicesOf(latest.current.lanes).flatMap((choice) =>
        choice.wire && !unchecked.has(choice.lane) ? [choice.wire] : [],
      );
      const format: ExportFormatWire =
        kind === "png" ? { format: "Png" } : { format: "Mp4", quality };
      jobs.start({ lanes, first, last, format, path: target });
    } catch (error) {
      setFailure(error instanceof Error ? error.message : String(error));
    } finally {
      setChoosing(false);
    }
  };
  const close = () => {
    jobs.dismiss();
    onClose();
  };

  const dialog = (() => {
    if (status.phase === "running")
      return (
        <Dialog
          title="Exporting…"
          okLabel="Export"
          onOk={() => undefined}
          onCancel={() => jobs.cancel()}
          busy
        >
          <Progress done={status.done} total={status.total} />
        </Dialog>
      );
    if (status.phase !== "idle")
      return (
        <Dialog title="Export" okLabel="Close" onOk={close}>
          <Outcome status={status} />
        </Dialog>
      );
    return (
      <Dialog
        title="Export"
        okLabel="Export"
        onOk={() => void start()}
        onCancel={close}
        busy={!ready || choosing}
      >
        <fieldset className="m-0 flex min-w-0 flex-col gap-s border-0 p-0">
          <legend className="p-0">Lanes</legend>
          {choices.map(({ lane, label, wire }) => (
            <Checkbox
              key={lane}
              label={label}
              checked={wire !== null && !unchecked.has(lane)}
              disabled={wire === null}
              onChange={(on) =>
                setUnchecked((now) => {
                  const next = new Set(now);
                  if (on) next.delete(lane);
                  else next.add(lane);
                  return next;
                })
              }
            />
          ))}
        </fieldset>
        <Select label="Format" value={kind} options={KINDS} onChange={setKind} />
        {kind === "mp4" && (
          <>
            <TextField label="From frame" value={from} onChange={setFrom} />
            <TextField label="To frame" value={to} onChange={setTo} />
            <Select label="Quality" value={quality} options={QUALITIES} onChange={setQuality} />
          </>
        )}
        {!bridge && <TextField label="Path on the server" value={path} onChange={setPath} />}
        {failure && (
          <p role="alert" className="m-0">
            {failure}
          </p>
        )}
      </Dialog>
    );
  })();
  // The page's body holds it, so a viewer that is hidden (a narrow window showing the sidebar) does not hide the
  // dialog. Its keys stay in it: the window's shortcuts do not run while it is open.
  return createPortal(
    <div className="contents" onKeyDown={(event) => event.stopPropagation()}>
      {dialog}
    </div>,
    document.body,
  );
}
