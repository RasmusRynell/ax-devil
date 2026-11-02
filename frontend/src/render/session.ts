/**
 * The page's side of the render protocol (crates/api-types/src/render.rs). The backend sends Setup; the page answers
 * Ready with the size of its pictures. Then, for each frame, the backend sends the Sample envelopes the page does not
 * hold yet, the frame's picture, and a Render; the page draws the picture with each lane's marks and answers with the
 * Pixels. Messages are handled in the order they arrive, which is what keeps a Render after its items.
 *
 * The page holds the Sample Items the previous Render named and nothing else, and draws each lane with the style the
 * backend froze into Setup, through the same `frameOf` as the viewer.
 */

import {
  RENDER_PROTOCOL,
  MATCH_RETAINED,
  type HostMessage,
  type LaneStyleWire,
  type OutputWire,
  type PageMessage,
  type RenderLaneWire,
} from "../generated/api";
import type { LaneFrame } from "../playback/contract";
import { frameOf } from "../playback/laneStyle";
import { EnvelopeError, parseEnvelope, type VideoFrame } from "../playback/protocol";
import { compileCatalog, readSample, type CompiledCatalog, type SampleView } from "../present";
import { outputOf, type Output } from "./output";

/** One socket to the backend. */
export interface Transport {
  send(data: string | Uint8Array<ArrayBuffer>): void;
  close(): void;
}

/** The GPU side: draws a picture with lane frames and reads the result back. */
export interface Stage {
  /** Sets up pictures of `size` read back as `output`, with `names` written on the lane cells; names the renderer. */
  open(output: OutputWire, size: Output, names: readonly string[] | null): string;
  /** Draws `frame` with one lane frame per lane (`undefined`: nothing to draw). */
  present(frame: VideoFrame, lanes: readonly (LaneFrame | undefined)[]): void;
  /** The Pixels message answering Render `seq` for what was drawn last; valid until the next call. */
  read(seq: number): Uint8Array<ArrayBuffer>;
  dispose(): void;
}

/** A lane as Setup describes it, with its catalog compiled. */
type Lane = { source: number | null; catalog: CompiledCatalog; style: LaneStyleWire };

/** Sample items by source and sample index. */
const keyOf = (source: number, sample: number): string => `${source}:${sample}`;

/** What a problem says, in the words Failed carries to the user. */
const reasonOf = (error: unknown): string =>
  error instanceof Error ? error.message : "The render page failed";

export class RenderSession {
  readonly #transport: Transport;
  readonly #secret: string;
  readonly #createStage: () => Stage;
  #stage: Stage | null = null;
  #picture = { width: 0, height: 0 };
  #lanes: Lane[] = [];
  /** The items held: those the last Render named and the ones sent since. */
  #items = new Map<string, { dataRevision: number; view: SampleView }>();
  /** The picture sent for the next Render. */
  #frame: VideoFrame | null = null;
  #failed = false;

  /** `createStage` makes the GPU side when Setup arrives, so a page with no WebGL2 can say so. */
  constructor(transport: Transport, secret: string, createStage: () => Stage) {
    this.#transport = transport;
    this.#secret = secret;
    this.#createStage = createStage;
  }

  /** Introduces the page to the backend; call it once the socket is open. */
  start(): void {
    this.#say({ type: "Hello", protocol: RENDER_PROTOCOL, secret: this.#secret });
  }

  /** Handles one message from the backend. After a failure the rest are ignored. */
  receive(data: string | ArrayBuffer): void {
    if (this.#failed) return;
    try {
      if (typeof data === "string") this.#text(JSON.parse(data) as HostMessage);
      else this.#binary(data);
    } catch (error) {
      this.#fail(
        error instanceof EnvelopeError
          ? `Bad message from the backend: ${error.message}`
          : reasonOf(error),
      );
    }
  }

  /** Frees the GPU side. */
  dispose(): void {
    this.#stage?.dispose();
    this.#stage = null;
  }

  #say(message: PageMessage): void {
    this.#transport.send(JSON.stringify(message));
  }

  #fail(reason: string): void {
    this.#failed = true;
    this.#say({ type: "Failed", reason });
    this.dispose();
    this.#transport.close();
  }

  #text(message: HostMessage): void {
    if (message.type === "Setup") this.#setup(message);
    else this.#render(message.seq, message.cells, message.kinds);
  }

  #setup(setup: Extract<HostMessage, { type: "Setup" }>): void {
    const lanes = setup.lanes.map((lane, at) => this.#lane(lane, at));
    if (lanes.length === 0) throw new Error("There are no lanes to draw");
    const { picture, max_output: limit } = setup;
    const size = outputOf(picture, lanes.length, setup.output);
    if (size.width > limit.width || size.height > limit.height)
      throw new Error(
        `The ${size.width}x${size.height} output is larger than the ${limit.width}x${limit.height} allowed`,
      );
    this.dispose();
    this.#lanes = lanes;
    this.#picture = picture;
    this.#items.clear();
    this.#frame = null;
    this.#stage = this.#createStage();
    const names = lanes.length > 1 ? lanes.map(({ style }) => style.label) : null;
    const renderer = this.#stage.open(setup.output, size, names);
    this.#say({ type: "Ready", width: size.width, height: size.height, renderer });
  }

  #lane({ source, catalog, style }: RenderLaneWire, at: number): Lane {
    const compiled = compileCatalog(catalog);
    if (!compiled.ok) {
      const first = compiled.diagnostics[0];
      throw new Error(
        `The catalog of lane ${style.label || at + 1} does not compile: ${first.pointer}: ${first.message}`,
      );
    }
    return { source, catalog: compiled.catalog, style };
  }

  #binary(data: ArrayBuffer): void {
    const envelope = parseEnvelope(data);
    if (!envelope) return;
    if (!this.#stage) throw new Error("Data arrived before Setup");
    if (envelope.kind === "frame") {
      const { frame } = envelope;
      if (frame.width !== this.#picture.width || frame.height !== this.#picture.height)
        throw new Error(
          `A ${frame.width}x${frame.height} picture arrived for ${this.#picture.width}x${this.#picture.height}`,
        );
      this.#frame = frame;
    } else if (envelope.kind === "sample") {
      this.#items.set(keyOf(envelope.source, envelope.sample), {
        dataRevision: envelope.dataRevision,
        view: readSample(envelope.item),
      });
    } else throw new Error(`A message of kind ${envelope.kind} is not part of the render protocol`);
  }

  #render(seq: number, cells: readonly number[], kinds: readonly number[]): void {
    const stage = this.#stage;
    const frame = this.#frame;
    const lanes = this.#lanes;
    if (!stage) throw new Error("Render arrived before Setup");
    if (!frame) throw new Error(`Render ${seq} arrived without a picture`);
    if (cells.length !== lanes.length || kinds.length !== lanes.length)
      throw new Error(`Render ${seq} names ${cells.length} lanes of ${lanes.length}`);
    const named = new Set<string>();
    const drawn = lanes.map(({ source, catalog, style }, at): LaneFrame | undefined => {
      const sample = cells[at];
      if (source === null || sample < 0) return undefined;
      const key = keyOf(source, sample);
      const item = this.#items.get(key);
      if (!item)
        throw new Error(
          `Render ${seq} needs sample ${sample} of source ${source}, which was not sent`,
        );
      named.add(key);
      return frameOf(style, {
        source,
        dataRevision: item.dataRevision,
        sample,
        columns: item.view,
        catalog,
        styleRevision: 0,
        retained: kinds[at] === MATCH_RETAINED,
      });
    });
    stage.present(frame, drawn);
    this.#transport.send(stage.read(seq));
    this.#frame = null;
    for (const key of this.#items.keys()) if (!named.has(key)) this.#items.delete(key);
  }
}
