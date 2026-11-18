import { useRef, useState, useSyncExternalStore, type ReactNode } from "react";
import { createPortal } from "react-dom";
import type { ExportFormatWire, ExportQualityWire } from "../generated/api";
import { Dialog, DialogBody } from "../kit/Dialog";
import { Form, FormCheck, FormInput, FormSection, FormSelect } from "../kit/Form";
import type { PlaybackView } from "../playback/view";
import { exportFile, usePickPath } from "../client/pickPath";
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

/** The dialog's title in every state. */
const TITLE = "Export Video";

/** `path` as it is when it ends in `.kind` (any case), otherwise with `.kind` appended: `clip.mov` becomes `clip.mov.mp4`. */
const withExtension = (path: string, kind: Kind): string =>
  path.toLowerCase().endsWith(`.${kind}`) ? path : `${path}.${kind}`;

/** A whole number from its text; `null` for anything else. */
const wholeNumber = (text: string): number | null =>
  /^\d+$/.test(text.trim()) ? Number(text) : null;

/** The dialog's box in every state: the answer button, then Cancel when `onCancel` is given. Escape runs `onCancel`, else the answer. */
function Screen({
  okLabel,
  busy = false,
  error,
  onOk,
  onCancel,
  children,
}: {
  okLabel: string;
  busy?: boolean;
  error?: string | null;
  onOk: () => void;
  onCancel?: () => void;
  children: ReactNode;
}) {
  return (
    <Dialog
      title={TITLE}
      okLabel={okLabel}
      onOk={onOk}
      onCancel={onCancel}
      busy={busy}
      error={error}
    >
      <DialogBody>
        <div className="flex w-panel max-w-full min-w-0 flex-col gap-m">{children}</div>
      </DialogBody>
    </Dialog>
  );
}

/** What an export that finished or could not start adds under the bar; why it failed is the dialog's error. */
function Outcome({ status }: { status: Exclude<ExportStatus, { phase: "idle" | "running" }> }) {
  if (status.phase === "failed")
    return status.location ? (
      <p className="m-0 text-small break-all text-muted">{status.location}</p>
    ) : null;
  return (
    <p role="status" className="m-0 break-all">
      {status.phase === "done" ? `Done: ${status.path}` : "Cancelled"}
    </p>
  );
}

/** The export's bar, with the percent done centered on it. */
function ProgressBar({ done, total }: { done: number; total: number }) {
  const percent = total > 0 ? Math.floor((done / total) * 100) : 0;
  return (
    <div
      role="progressbar"
      aria-label="Export progress"
      aria-valuemin={0}
      aria-valuemax={total}
      aria-valuenow={done}
      className="relative h-row w-full overflow-hidden rounded-control bg-line-strong"
    >
      <div className="h-full bg-accent" style={{ width: `${percent}%` }} />
      <span className="absolute inset-0 flex items-center justify-center text-on-canvas tabular-nums">
        {percent}%
      </span>
    </div>
  );
}

/** What an export asked for while a render catalog is still loading says; the export does not start. */
export function ExportNotice({ onClose }: { onClose: () => void }) {
  return createPortal(
    <div className="contents" onKeyDown={(event) => event.stopPropagation()}>
      <Dialog title={TITLE} onOk={onClose} focusOk>
        <p className="m-0">Render catalogs are still loading. Try again in a moment.</p>
      </Dialog>
    </div>,
    document.body,
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
  only,
  onClose,
}: {
  jobs: ExportJobs;
  view: PlaybackView;
  name: string;
  /** Offers just this lane, the one a lane menu was used on; every lane otherwise. */
  only?: number;
  onClose: () => void;
}) {
  const status = useSyncExternalStore(jobs.subscribe, jobs.getSnapshot);
  const latest = useRef(view);
  latest.current = view;
  const pick = usePickPath();
  const offered = (lanes: PlaybackView["lanes"]) =>
    choicesOf(lanes).filter((choice) => only === undefined || choice.lane === only);
  const choices = offered(view.lanes);
  const [unchecked, setUnchecked] = useState<ReadonlySet<number>>(new Set());
  const [kind, setKind] = useState<Kind>("mp4");
  const [quality, setQuality] = useState<ExportQualityWire>("balanced");
  const [from, setFrom] = useState("0");
  const [to, setTo] = useState(String(view.frameCount - 1));
  const [failure, setFailure] = useState<string | null>(null);
  const [choosing, setChoosing] = useState(false);

  const loading = choices.some((choice) => choice.cold === "loading");
  // With one lane there is no checkbox to bring it back, so it is always taken.
  const skipped = (all: readonly { lane: number }[], lane: number) =>
    all.length > 1 && unchecked.has(lane);
  const picked = choices.filter((choice) => choice.wire && !skipped(choices, choice.lane));
  const alone =
    choices.length === 1 && choices[0].cold === "failed" ? view.lanes[choices[0].lane] : null;
  const aloneStatus = alone?.status;
  const aloneWhy =
    alone &&
    (alone.catalogProblem ??
      (aloneStatus && aloneStatus !== "plain" && aloneStatus.status === "Failed"
        ? aloneStatus.reason
        : undefined) ??
      "Render catalog failed to load.");
  const first = kind === "png" ? view.playhead : wholeNumber(from);
  const last = kind === "png" ? view.playhead : wholeNumber(to);
  const framesOk = first !== null && last !== null && first <= last && last < view.frameCount;
  const ready = picked.length > 0 && framesOk && pick !== undefined;
  // Why a typed frame cannot be exported; nothing for the text a PNG ignores.
  const frameProblem = (frame: number | null) =>
    frame === null
      ? "Not a whole number"
      : frame >= view.frameCount
        ? "Past the last frame"
        : undefined;
  const orderProblem =
    first !== null && last !== null && first > last ? "After To frame" : undefined;

  const start = async () => {
    if (first === null || last === null) return;
    setFailure(null);
    setChoosing(true);
    try {
      // Named for the first lane taken, as the selection stands when Export is pressed; the viewer's name when none is.
      const source = (picked[0]?.label ?? name).split("/").filter(Boolean).pop() || name;
      const stem = source.replace(/\.[^.]*$/, "") || source;
      const chosen = await pick?.(exportFile(`${stem}.${kind}`, kind));
      if (!chosen) return;
      const target = withExtension(chosen, kind);
      // The lanes are read now: the picker may have been open while they changed.
      const now = offered(latest.current.lanes);
      const lanes = now.flatMap((choice) =>
        choice.wire && !skipped(now, choice.lane) ? [choice.wire] : [],
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
        <Screen okLabel="Export" onOk={() => undefined} onCancel={() => jobs.cancel()} busy>
          <ProgressBar done={status.done} total={status.total} />
          <span role="status" className="tabular-nums">
            {status.done > 0 ? `Frame ${status.done} / ${status.total}` : "Exporting…"}
          </span>
        </Screen>
      );
    if (status.phase !== "idle")
      return (
        <Screen
          okLabel="Close"
          onOk={close}
          error={status.phase === "failed" ? status.reason : null}
        >
          <ProgressBar done={status.done} total={status.total} />
          <Outcome status={status} />
        </Screen>
      );
    return (
      <Screen
        okLabel="Export"
        onOk={() => void start()}
        onCancel={close}
        busy={!ready || choosing}
        error={failure ?? aloneWhy}
      >
        <Form>
          {choices.length > 1 && (
            <FormSection heading="Lanes">
              {choices.map(({ lane, label, wire }) => (
                <FormCheck
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
            </FormSection>
          )}
          <FormSelect label="Format" value={kind} options={KINDS} onChange={setKind} />
          {kind === "mp4" && (
            <>
              <FormInput
                label="From frame"
                value={from}
                error={frameProblem(first) ?? orderProblem}
                onChange={setFrom}
              />
              <FormInput label="To frame" value={to} error={frameProblem(last)} onChange={setTo} />
              <FormSelect
                label="Quality"
                value={quality}
                options={QUALITIES}
                onChange={setQuality}
              />
            </>
          )}
        </Form>
        {loading && (
          <p role="status" className="m-0">
            Render catalogs are still loading.
          </p>
        )}
      </Screen>
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
