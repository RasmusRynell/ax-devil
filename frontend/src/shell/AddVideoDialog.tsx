import { useRef, useState } from "react";
import { Button } from "../kit/Button";
import { Dialog, DialogBody } from "../kit/Dialog";
import { TextField } from "../kit/Fields";
import { HandlersFailure, useHandlers } from "../resolvers/useHandlers";
import { useCommand } from "../session/session";
import { OverlayRow, undecided, type OverlayEntry, type OverlayRead } from "./OverlayRow";
import { Refusal } from "./SettingField";

const blankEntry = (key: number): OverlayEntry => ({ key, path: "", picked: "" });

/**
 * Asks for the path of a video on the machine that runs the backend, where this window has no file dialog of its own
 * to show: a remote server's window, or a browser, and any number of overlay files, each with the handler that reads
 * it and the options that handler declares. A blank overlay is left out. OK waits for a video and a handler for every
 * overlay; the backend's intake judges the paths and options, and its refusal stays here.
 */
export function AddVideoDialog({ onClose }: { onClose: () => void }) {
  const command = useCommand();
  const { handlers, failure: unavailable, retry } = useHandlers();
  const [path, setPath] = useState("");
  const [entries, setEntries] = useState<readonly OverlayEntry[]>([blankEntry(0)]);
  const [added, setAdded] = useState<number | null>(null);
  const [tried, setTried] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const nextKey = useRef(1);
  const readers = useRef(new Map<number, () => OverlayRead>());
  const decoders = handlers?.decoders ?? [];

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
      busy={busy || !path.trim() || entries.some(undecided)}
    >
      <DialogBody>
        <TextField
          label="Path on the server"
          value={path}
          onChange={(text) => {
            setPath(text);
            setFailure(null);
          }}
        />
        {handlers && (
          <>
            {entries.map((entry) => (
              <OverlayRow
                key={entry.key}
                entry={entry}
                decoders={decoders}
                focus={entry.key === added}
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
        {unavailable && <HandlersFailure failure={unavailable} retry={retry} />}
        {failure && <Refusal reason={failure} />}
      </DialogBody>
    </Dialog>
  );
}
