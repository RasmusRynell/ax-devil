/**
 * The playback socket's thread. It owns the WebSocket, parses the head of every message, and hands frames, the
 * timeline, Sample Items and Frame Maps to the main thread by transferring the received buffer, so the browser's one
 * copy per message is the only one. A frame sent as a JPEG is decoded to RGBA here first, in the order the `Inbox`
 * keeps. The main thread's rendering, clock and typing never queue behind receiving; the rules of the session live in
 * session.ts.
 */

import { decodeJpeg } from "./jpeg";
import type { ClientMessage } from "./protocol";
import { bufferOf, decode, Inbox, type Incoming, type Received } from "./wire";

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
  if (incoming.type === "closed") scope.close();
}

async function settle(received: Received): Promise<Incoming> {
  if (received.type !== "jpeg") return received;
  try {
    return {
      type: "envelope",
      envelope: { kind: "frame", frame: await decodeJpeg(received.frame) },
    };
  } catch (error) {
    return { type: "malformed", reason: `a JPEG frame does not decode: ${String(error)}` };
  }
}

const inbox = new Inbox(settle, deliver);

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
      if (incoming) inbox.push(incoming);
    };
    opened.onclose = ({ code }) => inbox.push({ type: "closed", code });
  } else if (data.type === "send") {
    const text = JSON.stringify(data.message);
    if (socket?.readyState === WebSocket.OPEN) socket.send(text);
    else held.push(text);
  } else {
    socket?.close();
  }
};
