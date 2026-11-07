import { useRef, useState } from "react";
import type { NewOverlayWire } from "../generated/api";
import { BrowseButton, Button } from "../kit/Button";
import { Dialog, DialogBody } from "../kit/Dialog";
import { TextField } from "../kit/Fields";
import { HandlersFailure, useHandlers } from "../resolvers/useHandlers";
import { useCommand } from "../session/session";
import { OverlayRow, fileName, undecided, type OverlayEntry, type OverlayRead } from "./OverlayRow";
import { Refusal } from "./SettingField";
import { useFileProblems } from "./useFileProblems";
import { useServerKind } from "./windowBridge";

const blankEntry = (key: number): OverlayEntry => ({ key, path: "", picked: "" });

/** What the form starts with: a video and its overlays, as an item holds them. */
export interface VideoDraft {
  path: string;
  label?: string | null;
  overlays: readonly NewOverlayWire[];
}

/**
 * Asks for a video on the machine that runs the backend, by its path, with a name that is the file's when left blank,
 * and any number of overlay files, each with the handler that reads it and the options that handler declares. The
 * desktop's own window can choose the video in a file dialog; a remote server's window or a browser types the path. A
 * blank overlay is left out. OK waits for a video that the backend finds readable, as it does each overlay, and a
 * handler for every overlay; the backend's intake judges the options, and its refusal stays here. `initial` fills the
 * form in, read once; with `editing`, the id of an item, OK replaces that item (it keeps its id and place) instead of
 * adding one.
 */
export function AddVideoDialog({
  initial,
  editing,
  onClose,
}: {
  initial?: VideoDraft;
  editing?: string;
  onClose: () => void;
}) {
  const command = useCommand();
  const { handlers, failure: unavailable, retry } = useHandlers();
  const [path, setPath] = useState(initial?.path ?? "");
  const [name, setName] = useState(initial?.label ?? "");
  const [entries, setEntries] = useState<readonly OverlayEntry[]>(() =>
    initial?.overlays.length
      ? initial.overlays.map(({ path, handler, label, options }, key) => ({
          key,
          path,
          picked: handler ?? "",
          label,
          options,
        }))
      : [blankEntry(0)],
  );
  const [added, setAdded] = useState<number | null>(null);
  const [tried, setTried] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const nextKey = useRef(entries.length);
  const readers = useRef(new Map<number, () => OverlayRead>());
  const decoders = handlers?.decoders ?? [];
  // Only the local backend's window may browse this machine's files.
  const bridge = useServerKind() === "local" ? window.axDevilWindow : undefined;
  const named = entries.map((entry) => entry.path.trim());
  const { problems, checking } = useFileProblems([
    ...new Set([path.trim(), ...named].filter(Boolean)),
  ]);
  const videoProblem = problems.get(path.trim());
  const browse = async () => {
    try {
      const chosen = (await bridge?.pickVideos()) ?? [];
      if (chosen.length > 1) setFailure("Select one video.");
      else if (chosen.length) {
        setPath(chosen[0]);
        setFailure(null);
      }
    } catch (error) {
      setFailure(error instanceof Error ? error.message : String(error));
    }
  };

  const edit = (entry: OverlayEntry) => {
    setEntries(entries.map((one) => (one.key === entry.key ? entry : one)));
    setFailure(null);
  };
  const ok = async () => {
    const overlays = entries.filter((entry) => entry.path.trim());
    const settings = overlays.map((entry) => readers.current.get(entry.key)?.().settings ?? null);
    if (settings.includes(null)) {
      setTried(true);
      return;
    }
    setBusy(true);
    try {
      const video = {
        type: "Video" as const,
        path: path.trim(),
        label: name.trim() || undefined,
        overlays: overlays.map((entry, at) => ({
          path: entry.path.trim(),
          handler: entry.picked,
          label: entry.label ?? null,
          options: settings[at] ?? {},
        })),
      };
      await command({
        type: "content",
        command: editing
          ? { type: "editItem", item: editing, new: video }
          : { type: "addItems", items: [video], open: true },
      });
      onClose();
    } catch (error) {
      setFailure(error instanceof Error ? error.message : String(error));
      setBusy(false);
    }
  };
  return (
    <Dialog
      title={editing ? "Edit video" : "Add video"}
      okLabel={editing ? "Save" : "Add"}
      onOk={() => void ok()}
      onCancel={onClose}
      busy={
        busy ||
        !path.trim() ||
        entries.some(undecided) ||
        checking ||
        [path.trim(), ...named].some((one) => problems.has(one))
      }
    >
      <DialogBody>
        <TextField
          label="Video path"
          value={path}
          onChange={(text) => {
            setPath(text);
            setFailure(null);
          }}
          action={bridge && <BrowseButton what="video file" onBrowse={() => void browse()} />}
        />
        {handlers && (
          <>
            {entries.map((entry, at) => (
              <OverlayRow
                number={at + 1}
                key={entry.key}
                entry={entry}
                decoders={decoders}
                focus={entry.key === added}
                problem={problems.get(entry.path.trim())}
                tried={tried}
                onChange={edit}
                onRemove={() => setEntries(entries.filter((one) => one.key !== entry.key))}
                register={(key, read) =>
                  read ? readers.current.set(key, read) : readers.current.delete(key)
                }
              />
            ))}
            <Button
              icon="plus"
              size="small"
              className="self-start"
              onClick={() => {
                setAdded(nextKey.current);
                setEntries([...entries, blankEntry(nextKey.current++)]);
              }}
            >
              Add overlay
            </Button>
          </>
        )}
        <TextField
          label="Name"
          value={name}
          placeholder={path.trim() ? fileName(path) : "Optional"}
          onChange={setName}
        />
        {!path.trim() && <Refusal reason="Choose a video file to continue." />}
        {videoProblem && <Refusal reason={videoProblem} />}
        {unavailable && <HandlersFailure failure={unavailable} retry={retry} />}
        {failure && <Refusal reason={failure} />}
      </DialogBody>
    </Dialog>
  );
}
