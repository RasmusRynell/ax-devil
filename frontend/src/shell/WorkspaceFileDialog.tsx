import { useState } from "react";
import { Dialog, DialogBody } from "../kit/Dialog";
import { TextField } from "../kit/Fields";
import { SUFFIX } from "../recents";
import { Refusal } from "../kit/Refusal";

interface Props {
  /** `save` asks where to save the workspace, starting from `path`; `open` asks which workspace file to open. */
  mode: "open" | "save";
  path?: string;
  /** Called with the path, which has the workspace suffix when saving; the backend judges it. */
  onSubmit: (path: string) => void;
  onClose: () => void;
}

/**
 * Asks for the path of a workspace file on the machine that runs the backend, where this window has no file dialog
 * of its own to show: a remote server's window, or a browser.
 */
export function WorkspaceFileDialog({ mode, path: start = "", onSubmit, onClose }: Props) {
  const [path, setPath] = useState(start);
  const [empty, setEmpty] = useState(false);
  const save = mode === "save";
  const ok = () => {
    const chosen = path.trim();
    if (!chosen) return setEmpty(true);
    onSubmit(save && !chosen.endsWith(SUFFIX) ? `${chosen}${SUFFIX}` : chosen);
    onClose();
  };
  return (
    <Dialog
      title={save ? "Save workspace as" : "Open workspace"}
      okLabel={save ? "Save" : "Open"}
      onOk={ok}
      onCancel={onClose}
    >
      <DialogBody>
        <TextField
          label="Path on the server"
          value={path}
          onChange={(text) => {
            setPath(text);
            setEmpty(false);
          }}
        />
        {empty && <Refusal reason="Enter a path on the server." />}
      </DialogBody>
    </Dialog>
  );
}
