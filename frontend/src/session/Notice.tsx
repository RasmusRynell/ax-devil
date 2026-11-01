import { Button } from "../kit/Button";
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

interface Props {
  connection: SessionState["connection"];
  ownerAway: boolean;
  ended: Ending | null;
  onDismiss: () => void;
}

/** The bar along the bottom that says the session is not as it should be; absent while all is well. */
export function Banner({ connection, ownerAway, ended, onDismiss }: Props) {
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
  return (
    <div
      role={ended ? "alert" : "status"}
      className="fixed inset-x-0 bottom-0 z-40 flex items-center justify-center gap-m border-t border-line bg-popup p-s"
    >
      {text}
      {ended && <Button onClick={onDismiss}>Dismiss</Button>}
    </div>
  );
}
