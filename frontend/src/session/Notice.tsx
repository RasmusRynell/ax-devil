import { useState } from "react";
import { credential, holdPassword, PASSWORD_RULE, sendable } from "../access";
import { Button } from "../kit/Button";
import { Dialog } from "../kit/Dialog";
import { TextField } from "../kit/Fields";
import { useServerKind } from "../shell/windowBridge";
import type { Ending } from "./connection";
import type { SessionState } from "./store";

/** What each way a session or a connection can end says to the person in it. */
export const ENDINGS: Record<Ending, string> = {
  stopped: "The owner stopped sharing.",
  ownerGone: "The owner left and did not come back.",
  replaced: "Opened in another tab.",
  unknown: "That session has ended.",
  invalid: "That link or password is not valid.",
  limited: "Too many messages: the connection was closed.",
  full: "That session or server is full.",
  refused: "The server refused this session.",
};

/** What a browser that opened a join link sees once its session is over: there is nothing else to show. */
export function EndedScreen({ why }: { why: Ending }) {
  // A desktop window on another machine's server has no title bar here, so this is its only way back.
  const remote = useServerKind() === "remote";
  return (
    <main
      role="alert"
      className="flex h-screen flex-col items-center justify-center gap-m p-m text-center"
    >
      {ENDINGS[why]}
      {remote && (
        <Button onClick={() => void window.axDevilWindow?.disconnect()}>Disconnect</Button>
      )}
    </main>
  );
}

/**
 * Asks for the server password and loads the page anew with it, so everything is fetched under the new one. Without
 * `onClose` there is nothing to go back to: the page cannot be used before a password is given.
 */
function PasswordDialog({ onClose }: { onClose?: () => void }) {
  const [password, setPassword] = useState("");
  const [failure, setFailure] = useState<string | null>(null);
  const ok = () => {
    if (!sendable(password)) return setFailure(`That password cannot be used. ${PASSWORD_RULE}`);
    holdPassword(password);
    window.location.reload();
  };
  return (
    <Dialog title="Server password" onOk={ok} onCancel={onClose}>
      <TextField
        label="Password"
        masked
        value={password}
        onChange={(text) => {
          setPassword(text);
          setFailure(null);
        }}
      />
      {failure && (
        <p role="alert" className="m-0">
          {failure}
        </p>
      )}
    </Dialog>
  );
}

/**
 * What a desktop window on another machine's server offers once that server turned its password down. Dismissing
 * would try the same password again, so the way out is a new password or back to the local backend.
 */
function RejectedPassword() {
  const [asking, setAsking] = useState(false);
  return (
    <>
      {credential()?.kind === "password" && (
        <Button onClick={() => setAsking(true)}>Change password</Button>
      )}
      <Button onClick={() => void window.axDevilWindow?.disconnect()}>Disconnect</Button>
      {asking && <PasswordDialog onClose={() => setAsking(false)} />}
    </>
  );
}

interface Props {
  connection: SessionState["connection"];
  ownerAway: boolean;
  ended: Ending | null;
  onDismiss: () => void;
}

/** The bar along the bottom that says the session is not as it should be; absent while all is well. */
export function Banner({ connection, ownerAway, ended, onDismiss }: Props) {
  const remote = useServerKind() === "remote";
  const text = ended
    ? ENDINGS[ended]
    : connection === "connecting"
      ? "Connecting…"
      : connection === "reconnecting"
        ? "Connection lost. Reconnecting…"
        : ownerAway
          ? "Owner reconnecting…"
          : null;
  if (!text) return null;
  // A server that has a password turns away a visit that brings none or the wrong one, and Dismiss would only visit again.
  const password =
    ended === "invalid" ? credential()?.kind === "password" : ended === "refused" && !credential();
  return (
    <div
      role={ended ? "alert" : "status"}
      className="fixed inset-x-0 bottom-0 z-40 flex items-center justify-center gap-m border-t border-line bg-popup p-s"
    >
      {text}
      {ended === "invalid" && remote ? (
        <RejectedPassword />
      ) : password ? (
        <PasswordDialog />
      ) : (
        ended && <Button onClick={onDismiss}>Dismiss</Button>
      )}
    </div>
  );
}
