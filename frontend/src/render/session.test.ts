// @vitest-environment node
import { describe, expect, it } from "vite-plus/test";
import type {
  HostMessage,
  LaneStyleWire,
  OutputWire,
  PageMessage,
  RenderLaneWire,
} from "../generated/api";
import { catalogDocument, frameMessage, sampleBytes } from "../playback/fakeData";
import type { LaneFrame } from "../playback/contract";
import { MATCH_EXACT, MATCH_RETAINED } from "../playback/protocol";
import type { Output } from "./output";
import { RenderSession, type Stage, type Transport } from "./session";

const style = (label: string, extra: Partial<LaneStyleWire> = {}): LaneStyleWire => ({
  label,
  enabled_classes: [0],
  class_options: 1,
  hidden: 0,
  theme: null,
  retained_opacity: 0.4,
  filter: null,
  ...extra,
});

const lane = (source: number | null, label = `lane ${source}`, extra = {}): RenderLaneWire => ({
  source,
  catalog: catalogDocument(),
  style: style(label, extra),
});

type Drawn = { frame: { width: number }; lanes: readonly (LaneFrame | undefined)[] };

/** A session on a fake socket and a fake GPU, so what the page says and draws can be read back. */
function page() {
  const said: PageMessage[] = [];
  const sent: Uint8Array[] = [];
  const opened: { output: OutputWire; size: Output; names: readonly string[] | null }[] = [];
  const drawn: Drawn[] = [];
  const transport: Transport = {
    send: (data) => (typeof data === "string" ? said.push(JSON.parse(data)) : sent.push(data)),
    close: () => undefined,
  };
  const stage: Stage = {
    open: (output, size, names) => (opened.push({ output, size, names }), "fake"),
    present: (frame, lanes) => drawn.push({ frame, lanes }),
    read: (seq) => Uint8Array.of(seq),
    dispose: () => undefined,
  };
  const session = new RenderSession(transport, "secret", () => stage);
  const send = (message: HostMessage) => session.receive(JSON.stringify(message));
  const setup = (
    lanes: RenderLaneWire[],
    picture = { width: 4, height: 2 },
    max = 3840,
    output: OutputWire = "I420",
  ) =>
    send({
      type: "Setup",
      output,
      picture,
      max_output: { width: max, height: max },
      lanes,
    });
  const render = (seq: number, cells: number[], kinds = cells.map(() => MATCH_EXACT)) =>
    send({ type: "Render", seq, cells, kinds });
  const picture = () => session.receive(frameMessage(4, 2, new Uint8Array(32)));
  const sample = (source: number, index: number) =>
    session.receive(sampleBytes({ source, sample: index }));
  return { said, sent, opened, drawn, session, setup, render, picture, sample };
}

describe("render session", () => {
  it("proves itself, then answers Setup with the size of its pictures", () => {
    const sizes: [{ width: number; height: number }, number, OutputWire, number, number][] = [
      [{ width: 200, height: 100 }, 1, "I420", 200, 100],
      [{ width: 201, height: 101 }, 1, "I420", 200, 100],
      [{ width: 201, height: 101 }, 1, "Rgba", 201, 101],
      [{ width: 200, height: 100 }, 2, "I420", 400, 100],
      [{ width: 200, height: 100 }, 4, "I420", 400, 200],
      [{ width: 3840, height: 2160 }, 4, "I420", 3840, 2160],
      [{ width: 7680, height: 4320 }, 1, "I420", 7680, 4320],
    ];
    for (const [picture, lanes, output, width, height] of sizes) {
      const { said, session, setup } = page();
      session.start();
      setup(
        Array.from({ length: lanes }, (_, i) => lane(i)),
        picture,
        16384,
        output,
      );
      expect(said).toEqual([
        { type: "Hello", protocol: 1, secret: "secret" },
        { type: "Ready", width, height, renderer: "fake" },
      ]);
    }
  });

  it("names the lanes on their cells only when several share the picture", () => {
    const { opened, setup } = page();
    setup([lane(0, "front")]);
    setup([lane(0, "front"), lane(1, "rear")]);
    expect(opened.map(({ names }) => names)).toEqual([null, ["front", "rear"]]);
  });

  it("refuses an output over the limit and a catalog that does not compile", () => {
    const over = page();
    over.setup([lane(0)], { width: 200, height: 100 }, 100);
    expect(over.said[0]).toMatchObject({ type: "Failed" });
    expect(over.opened).toEqual([]);

    const broken = page();
    broken.setup([{ ...lane(0), catalog: "{}" }]);
    expect(broken.said[0]).toMatchObject({ type: "Failed" });
  });

  it("draws each lane with its frozen style over the sample its Render names", () => {
    const { drawn, sent, setup, picture, sample, render } = page();
    setup([
      lane(0, "a", { hidden: 5, theme: "night", filter: "score > 0.5" }),
      lane(1, "b", { enabled_classes: [] }),
      lane(2, "c", { filter: "not a filter (" }),
    ]);
    sample(0, 3);
    sample(1, 3);
    sample(2, 3);
    picture();
    render(8, [3, 3, 3], [MATCH_RETAINED, MATCH_EXACT, MATCH_EXACT]);
    expect(sent).toEqual([Uint8Array.of(8)]);
    const [first, second, third] = drawn[0].lanes;
    expect(first?.filter).not.toBeNull();
    expect(second?.filter).toBeNull();
    expect(third?.filter).toBeNull();
    expect(first).toMatchObject({ source: 0, sample: 3, hidden: 5, theme: "night", opacity: 0.4 });
    expect(first?.classMask).toEqual({ enabled: Uint32Array.of(1), options: 1 });
    expect(second).toMatchObject({ source: 1, hidden: 0, theme: null, opacity: 1 });
    expect(second?.classMask.enabled).toEqual(Uint32Array.of(0));
  });

  it("draws nothing for a lane with no cell, or no source", () => {
    const { drawn, setup, picture, sample, render } = page();
    setup([lane(0), lane(null), lane(2)]);
    sample(0, 1);
    picture();
    render(1, [1, 1, -1]);
    expect(drawn[0].lanes.map((frame) => frame?.sample)).toEqual([1, undefined, undefined]);
  });

  it("keeps the items a Render named and drops the rest", () => {
    const { said, drawn, setup, picture, sample, render } = page();
    setup([lane(0)]);
    sample(0, 1);
    picture();
    render(1, [1]);
    // The host sends nothing new; the item the last Render named is still held.
    picture();
    render(2, [1]);
    expect(drawn.map(({ lanes }) => lanes[0]?.sample)).toEqual([1, 1]);
    sample(0, 2);
    picture();
    render(3, [2]);
    // Sample 1 was not named by Render 3, so it is gone.
    picture();
    render(4, [1]);
    expect(drawn).toHaveLength(3);
    expect(said.at(-1)).toMatchObject({ type: "Failed" });
  });

  it("fails a Render that comes before its picture or its items, and then stops", () => {
    const early = page();
    early.setup([lane(0)]);
    early.sample(0, 1);
    early.render(1, [1]);
    expect(early.said.at(-1)).toMatchObject({ type: "Failed" });

    const missing = page();
    missing.setup([lane(0)]);
    missing.picture();
    missing.render(1, [1]);
    expect(missing.said.at(-1)).toMatchObject({ type: "Failed" });
    const count = missing.said.length;
    missing.sample(0, 1);
    missing.render(2, [1]);
    expect(missing.said).toHaveLength(count);
    expect(missing.sent).toEqual([]);
  });
});
