import { useState } from "react";
import { PASSWORD_RULE, sendable } from "../client/access";
import { Dialog } from "../kit/Dialog";
import { TextField } from "../kit/Fields";
import "../client/windowBridge";

/** True for a server address (http or https, no fragment): the one kind of target a password goes with. */
function isPlainServer(text: string): boolean {
  try {
    const url = new URL(text.trim());
    return (url.protocol === "http:" || url.protocol === "https:") && !url.hash;
  } catch {
    return false;
  }
}

/** What the shell said, without Electron's "Error invoking remote method" wrapper. */
function reasonOf(error: unknown): string {
  const message = error instanceof Error ? error.message : String(error);
  return message.replace(/^Error invoking remote method '[^']*': (Error: )?/, "");
}

/**
 * Asks for another machine's server, or a join link to one of its sessions, and hands it to the desktop shell, which
 * replaces this window with one on that server. A password is asked for only with a plain server address; a link
 * carries its own credential. The shell judges the address: its refusal stays in the dialog, and a server that then
 * fails to load is answered by the shell's own dialog.
 */
export function ConnectDialog({ onClose }: { onClose: () => void }) {
  const [target, setTarget] = useState("");
  const [password, setPassword] = useState("");
  const [failure, setFailure] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const plain = isPlainServer(target);
  const ok = async () => {
    if (!target.trim()) {
      setFailure("Enter a server address or a join link.");
      return;
    }
    if (plain && password && !sendable(password)) {
      setFailure(`That password cannot be used. ${PASSWORD_RULE}`);
      return;
    }
    setBusy(true);
    try {
      await window.axDevilWindow?.connect?.(
        target.trim(),
        plain && password ? password : undefined,
      );
      onClose();
    } catch (error) {
      setFailure(reasonOf(error));
      setBusy(false);
    }
  };
  return (
    <Dialog title="Connect to server" onOk={() => void ok()} onCancel={onClose} busy={busy}>
      <TextField
        label="Server address or join link"
        value={target}
        onChange={(text) => {
          setTarget(text);
          setFailure(null);
        }}
      />
      {plain && <TextField label="Password" masked value={password} onChange={setPassword} />}
      {failure && (
        <p role="alert" className="m-0">
          {failure}
        </p>
      )}
    </Dialog>
  );
}
