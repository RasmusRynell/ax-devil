import { useRef, useState } from "react";
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

/**
 * Asks for a video on the machine that runs the backend, by its path, with a name that is the file's when left blank,
 * and any number of overlay files, each with the handler that reads it and the options that handler declares. The
 * desktop's own window can choose the video in a file dialog; a remote server's window or a browser types the path. A
 * blank overlay is left out. OK waits for a video that the backend finds readable, as it does each overlay, and a
 * handler for every overlay; the backend's intake judges the options, and its refusal stays here.
 */
export function AddVideoDialog({ onClose }: { onClose: () => void }) {
  const command = useCommand();
  const { handlers, failure: unavailable, retry } = useHandlers();
  const [path, setPath] = useState("");
  const [name, setName] = useState("");
  const [entries, setEntries] = useState<readonly OverlayEntry[]>([blankEntry(0)]);
  const [added, setAdded] = useState<number | null>(null);
  const [tried, setTried] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const nextKey = useRef(1);
  const readers = useRef(new Map<number, () => OverlayRead>());
  const decoders = handlers?.decoders ?? [];
  // Only the local backend's window may browse this machine's files.
  const bridge = useServerKind() === "local" ? window.axDevilWindow : undefined;
  const named = entries.map((entry) => entry.path.trim());
  const { problems, checking } = useFileProblems([
    ...new Set([path.trim(), ...named].filter(Boolean)),
  ]);
  const videoProblem = problems.get(path.trim());
  const browse = () =>
    void bridge
      ?.pickVideos()
      .then((chosen) => {
        if (chosen.length > 1) setFailure("Select one video.");
        else if (chosen.length) {
          setPath(chosen[0]);
          setFailure(null);
        }
      })
      .catch((error: unknown) =>
        setFailure(error instanceof Error ? error.message : String(error)),
      );

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
      await command({
        type: "content",
        command: {
          type: "addItems",
          items: [
            {
              type: "Video",
              path: path.trim(),
              label: name.trim() || undefined,
              overlays: overlays.map((entry, at) => ({
                path: entry.path.trim(),
                handler: entry.picked,
                label: null,
                options: settings[at] ?? {},
              })),
            },
          ],
          open: true,
        },
      });
      onClose();
    } catch (error) {
      setFailure(error instanceof Error ? error.message : String(error));
      setBusy(false);
    }
  };
  return (
    <Dialog
      title="Add video"
      okLabel="Add"
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
          action={bridge && <BrowseButton what="video file" onBrowse={browse} />}
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
