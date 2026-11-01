/**
 * The playback socket's thread. It owns the WebSocket, parses every message, and hands each frame to the main thread
 * by transferring the received buffer, so the browser's one copy per message is the only one. The main thread's
 * rendering, clock and typing never queue behind receiving; the rules of the session live in session.ts.
 */

import type { ClientMessage } from "./protocol";
import { bufferOf, decode, type Incoming } from "./wire";

export type ToWorker =
  | { type: "connect"; url: string }
  | { type: "send"; message: ClientMessage }
  | { type: "close" };

interface Scope {
  onmessage: ((event: MessageEvent<ToWorker>) => void) | null;
  postMessage(message: Incoming, transfer: Transferable[]): void;
  close(): void;
}
const scope = self as unknown as Scope;

let socket: WebSocket | undefined;
let held: string[] = [];

function deliver(incoming: Incoming): void {
  const transfer = incoming.type === "envelope" ? [bufferOf(incoming.envelope) as ArrayBuffer] : [];
  scope.postMessage(incoming, transfer);
}

scope.onmessage = ({ data }) => {
  if (data.type === "connect") {
    const opened = new WebSocket(data.url);
    socket = opened;
    opened.binaryType = "arraybuffer";
    opened.onopen = () => {
      held.forEach((text) => opened.send(text));
      held = [];
    };
    opened.onmessage = ({ data: received }: MessageEvent<string | ArrayBuffer>) => {
      const incoming = decode(received);
      if (incoming) deliver(incoming);
    };
    opened.onclose = ({ code }) => {
      deliver({ type: "closed", code });
      scope.close();
    };
  } else if (data.type === "send") {
    const text = JSON.stringify(data.message);
    if (socket?.readyState === WebSocket.OPEN) socket.send(text);
    else held.push(text);
  } else {
    socket?.close();
  }
};
