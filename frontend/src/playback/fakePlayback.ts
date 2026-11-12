// A playback stream against a fake server, a fake display refresh and a fake clock, for tests.
import { opened as openedMessage } from "../lanes/fakeOpened";
import { catalogDocument, mapBytes, sampleBytes } from "../stream/fakeData";
import type { LaneFrame } from "../lanes/contract";
import { PlaybackStream } from "./stream";
import type { PlaybackIntent, StreamDeps } from "./streamDeps";
import type {
  ClientMessage,
  LaneInfo,
  LaneStatusWire,
  ServerMessage,
  VideoFrame,
} from "../stream/protocol";
import { decode, type Incoming } from "../stream/wire";

export const FRAME_BYTES = 100;

export type OpenedMessage = Extract<ServerMessage, { type: "Opened" }>;

/** Lets the ticket the stream asked for arrive, so it can open its socket. */
export const flush = () => new Promise<void>((resolve) => setTimeout(resolve, 0));

export function videoFrame(sequenceId: number): VideoFrame {
  const empty = new Uint8Array(0);
  return {
    sequenceId,
    timestampUs: sequenceId * 40_000,
    keyframe: false,
    last: false,
    width: 4,
    height: 4,
    format: "i420",
    matrix: "bt709",
    fullRange: false,
    strides: [4, 2, 2],
    planes: [empty, empty, empty],
    byteLength: FRAME_BYTES,
  };
}

export const opened = (frames = 10, lanes?: LaneInfo[]): ServerMessage =>
  openedMessage(frames, FRAME_BYTES, lanes);

/** A stream against a fake server, a fake display refresh and a fake clock, with a window of ten frames. */
export async function setup(
  resume?: { frame: number; speed: number },
  deps: Partial<StreamDeps> = {},
) {
  const sent: ClientMessage[] = [];
  const intents: PlaybackIntent[] = [];
  let tickets = 0;
  let ticket: () => Promise<string> = async () => `ticket-${++tickets}`;
  const presented: number[] = [];
  const drawn: { sequenceId: number; lanes: (LaneFrame | undefined)[] }[] = [];
  const time = { now: 1000 };
  const refreshes: (() => void)[] = [];
  const retries: { run: () => void; ms: number }[] = [];
  let deliver: (incoming: Incoming) => void = () => {};
  let connections = 0;
  let closedConnections = 0;
  const stream = new PlaybackStream({
    ticket: () => ticket(),
    onIntent: (intent) => intents.push(intent),
    connect: (onIncoming) => {
      connections++;
      deliver = onIncoming;
      return { send: (message) => sent.push(message), close: () => closedConnections++ };
    },
    now: () => time.now,
    frame: (callback) => {
      refreshes.push(callback);
      return () => void refreshes.splice(refreshes.indexOf(callback) >>> 0, 1);
    },
    after: (run, ms) => {
      const retry = { run, ms };
      retries.push(retry);
      return () => void retries.splice(retries.indexOf(retry) >>> 0, 1);
    },
    budgetBytes: 10 * FRAME_BYTES,
    resume,
    ...deps,
  });
  stream.setSurface({
    present: (frame, lanes) => {
      presented.push(frame.sequenceId);
      drawn.push({ sequenceId: frame.sequenceId, lanes: [...lanes] });
    },
  });
  const server = {
    say: (message: ServerMessage) => deliver({ type: "message", message }),
    frames: (...ids: number[]) =>
      ids.forEach((id) =>
        deliver({ type: "envelope", envelope: { kind: "frame", frame: videoFrame(id) } }),
      ),
    timeline: (frames = 10) =>
      deliver({
        type: "envelope",
        envelope: {
          kind: "timeline",
          times: Float64Array.from({ length: frames }, (_, id) => id * 40_000),
        },
      }),
    drop: (code = 1006) => deliver({ type: "closed", code }),
    open: (frames = 10, lanes?: LaneInfo[]) => {
      server.say(opened(frames, lanes));
      server.timeline(frames);
    },
    status: (lane: number, status: LaneStatusWire) =>
      server.say({ type: "LaneStatus", lane, status }),
    /** A Sample Item of `source`; a real one from the oracle fixtures unless `fields.item` says otherwise. */
    sample: (fields: Parameters<typeof sampleBytes>[0] = {}) => {
      const incoming = decode(sampleBytes(fields));
      if (incoming && incoming.type !== "jpeg") deliver(incoming);
    },
    /** The data window the backend applied: the frames Frame Map cells are kept for, inclusive. */
    window: (first: number, last: number) => server.say({ type: "DataWindow", first, last }),
    /** A run of a lane's Frame Map. */
    map: (fields: Parameters<typeof mapBytes>[0]) => {
      const incoming = decode(mapBytes(fields));
      if (incoming && incoming.type !== "jpeg") deliver(incoming);
    },
    /** The answer to a `Catalog` request: Standard unless `document` says otherwise. */
    catalog: (
      id = "built-in:standard",
      document: string | null = catalogDocument(),
      revision = 1,
    ) => server.say({ type: "Catalog", id, revision, document, problem: null }),
  };
  /** One display refresh after `ms` milliseconds. */
  const refresh = (ms = 0) => {
    time.now += ms;
    // Display refresh callbacks run once, as requestAnimationFrame's do.
    refreshes.splice(0).forEach((callback) => callback());
  };
  const wants = () => sent.filter((message) => message.type === "Want");
  stream.start();
  await flush();
  return {
    stream,
    server,
    sent,
    intents,
    tickets: (next: () => Promise<string>) => (ticket = next),
    presented,
    drawn,
    refresh,
    wants,
    retries,
    refreshes,
    connections: () => connections,
    closedConnections: () => closedConnections,
  };
}
