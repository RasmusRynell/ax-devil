import { useState } from "react";
import { PASSWORD_RULE, sendable } from "../client/access";
import { Dialog, DialogBody } from "../kit/Dialog";
import { Form, FormInput } from "../kit/Form";
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
 * carries its own credential. Connect waits for an address without saying so before anything is wrong. The shell
 * judges the address: its refusal stays in the dialog, and a server that then fails to load is answered by the
 * shell's own dialog.
 */
export function ConnectDialog({ onClose }: { onClose: () => void }) {
  const [target, setTarget] = useState("");
  const [password, setPassword] = useState("");
  const [failure, setFailure] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  // Connect was pressed with no address: the field says so.
  const [attempted, setAttempted] = useState(false);
  const plain = isPlainServer(target);
  const ok = async () => {
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
  const missing = !target.trim();
  return (
    <Dialog
      title="Connect to Server"
      okLabel="Connect"
      onOk={() => void ok()}
      onCancel={onClose}
      busy={busy}
      incomplete={missing}
      onIncomplete={() => setAttempted(true)}
      error={failure}
    >
      <DialogBody>
        <div className="w-panel max-w-full min-w-0">
          <Form>
            <FormInput
              label="Server address or join link"
              value={target}
              placeholder="Required"
              required
              error={attempted && missing ? "Required" : undefined}
              onChange={(text) => {
                setTarget(text);
                setFailure(null);
              }}
            />
            {plain && (
              <FormInput
                label="Password"
                masked
                value={password}
                onChange={(text) => {
                  setPassword(text);
                  setFailure(null);
                }}
              />
            )}
          </Form>
        </div>
      </DialogBody>
    </Dialog>
  );
}
