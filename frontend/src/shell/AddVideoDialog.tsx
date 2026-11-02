import { useState } from "react";
import { useCommand } from "../session/session";
import { Dialog } from "../kit/Dialog";
import { TextField } from "../kit/Fields";
import { Refusal } from "./SettingField";

/**
 * Asks for the path of a video on the machine that runs the backend, where this window has no file dialog of its own
 * to show: a remote server's window, or a browser. The backend's intake judges the path; its refusal stays here.
 */
export function AddVideoDialog({ onClose }: { onClose: () => void }) {
  const command = useCommand();
  const [path, setPath] = useState("");
  const [failure, setFailure] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const ok = async () => {
    if (!path.trim()) {
      setFailure("Enter a path on the server.");
      return;
    }
    setBusy(true);
    try {
      await command({
        type: "content",
        command: {
          type: "addItems",
          items: [{ type: "Video", path: path.trim(), overlays: [] }],
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
    <Dialog title="Add video" okLabel="Add" onOk={() => void ok()} onCancel={onClose} busy={busy}>
      <TextField
        label="Path on the server"
        value={path}
        onChange={(text) => {
          setPath(text);
          setFailure(null);
        }}
      />
      {failure && <Refusal reason={failure} />}
    </Dialog>
  );
}
