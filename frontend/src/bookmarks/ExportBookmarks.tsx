import { useState, type ReactNode } from "react";
import { Dialog, DialogBody } from "../kit/Dialog";
import { TextField } from "../kit/Fields";
import { useCommand, useDoc } from "../session/session";
import { Refusal } from "../shell/SettingField";
import { useServerKind } from "../shell/windowBridge";

/**
 * Exporting every bookmark to a CSV file on the machine that runs the backend: the function that starts it, and the
 * dialog it may need to show. The desktop's own save dialog picks the file when this window has one; any other window
 * types a path. A failure goes to `onFail`.
 */
export function useExportBookmarks(onFail: (error: unknown) => void): [() => void, ReactNode] {
  const command = useCommand();
  const name = `${useDoc()?.workspace.name ?? "Untitled"} bookmarks.csv`;
  const bridge = useServerKind() === "local" ? window.axDevilWindow : undefined;
  const [asking, setAsking] = useState(false);
  const [path, setPath] = useState("");
  const [empty, setEmpty] = useState(false);
  const write = (chosen: string) =>
    command({ type: "workspace", command: { type: "exportBookmarks", path: chosen } }).catch(
      onFail,
    );
  const start = () => {
    if (bridge)
      bridge
        .pickExportFile(name, "csv")
        .then((chosen) => (chosen ? write(chosen) : undefined), onFail);
    else setAsking(true);
  };
  const dialog = asking && (
    <Dialog
      title="Export CSV"
      okLabel="Export"
      onOk={() => {
        const chosen = path.trim();
        if (!chosen) return setEmpty(true);
        void write(chosen);
        setAsking(false);
      }}
      onCancel={() => setAsking(false)}
    >
      <DialogBody>
        <TextField
          label="Path on the server"
          value={path}
          placeholder={name}
          onChange={(text) => {
            setPath(text);
            setEmpty(false);
          }}
        />
        {empty && <Refusal reason="Enter a path on the server." />}
      </DialogBody>
    </Dialog>
  );
  return [start, dialog];
}
