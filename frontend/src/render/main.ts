/**
 * Entry of `render.html`, the hidden page that draws an export's pictures. The backend starts the page with
 * `#port=<loopback port>&secret=<hex>` in its address; the page opens `ws://127.0.0.1:<port>/` and speaks the render
 * protocol on it (see `session.ts`). Nothing else loads it.
 */

import { RenderSession } from "./session";
import { GlStage } from "./stage";

const params = new URLSearchParams(location.hash.slice(1));
const socket = new WebSocket(`ws://127.0.0.1:${params.get("port")}/`);
socket.binaryType = "arraybuffer";
const session = new RenderSession(
  { send: (data) => socket.send(data), close: () => socket.close() },
  params.get("secret") ?? "",
  () => new GlStage(),
);
socket.addEventListener("open", () => session.start());
socket.addEventListener("message", (event: MessageEvent<string | ArrayBuffer>) =>
  session.receive(event.data),
);
socket.addEventListener("close", () => session.dispose());
