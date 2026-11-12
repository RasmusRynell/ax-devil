import type { ToWorker } from "./worker";
import type { Connect, Incoming } from "./wire";

/** The playback socket on the page's own host, `ws` or `wss` after the page's scheme. */
function playbackUrl(): string {
  const scheme = location.protocol === "https:" ? "wss" : "ws";
  return `${scheme}://${location.host}/api/playback`;
}

/** Opens `/api/playback` in a worker of its own, which ends with the socket. */
export const connectWorker: Connect = (onIncoming) => {
  const worker = new Worker(new URL("./worker.ts", import.meta.url), { type: "module" });
  const post = (message: ToWorker) => worker.postMessage(message);
  worker.onmessage = ({ data }: MessageEvent<Incoming>) => {
    onIncoming(data);
    if (data.type === "closed") worker.terminate();
  };
  post({ type: "connect", url: playbackUrl() });
  return {
    send: (message) => post({ type: "send", message }),
    close: () => post({ type: "close" }),
  };
};
