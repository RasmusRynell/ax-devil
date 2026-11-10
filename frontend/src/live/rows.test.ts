import { describe, expect, it } from "vite-plus/test";
import type { ItemState, NetworkRow } from "../generated/api";
import { scenario } from "../session/fakeScenario";
import { camerasOf, streamingCount } from "./cameras";
import { facetKey, offeredFacets, sectionsOf, viewsOf, type Section } from "./rows";
import { statusOf, type StreamStatus } from "./streamState";

const scanned = (address: string, extra: Partial<NetworkRow> = {}): NetworkRow => ({
  address,
  scheme: "https",
  port: 443,
  identity: { model: "Q1656", serial: "S1", firmware: "11.8.1", product_type: "Box Camera" },
  uptime_s: 90_000,
  sign_in: { state: "NotTried" },
  ...extra,
});

const cameras = scenario("cameras");
const WORKSPACE = cameras.doc().workspace.items;
const { a: A, b: B } = cameras.ids;

const sections = (reported: Map<string, StreamStatus>, found: NetworkRow[] = []): Section[] =>
  sectionsOf({
    cameras: camerasOf(WORKSPACE),
    found,
    scanned: found.length > 0,
    reported,
    checks: new Map(),
    thumbnails: true,
    viewed: new Set([A]),
    order: "address",
  });

describe("camera rows", () => {
  it("shows a camera by its most urgent stream, and its streams by what they draw", () => {
    const failed = new Map([
      [A, { state: "failed", text: "Login rejected", anyLive: false }],
    ] as const);
    const [live] = sections(failed);
    const [camera] = live?.rows ?? [];
    expect(camera).toMatchObject({
      label: "Lobby",
      aside: "Login rejected",
      mark: { icon: "triangle-alert", color: "caution" },
      action: "addStream",
    });
    const [first, second] = camera?.children ?? [];
    expect(first).toMatchObject({
      label: "scene",
      tag: "MQTT",
      detail: "Login rejected",
      detailWarning: true,
      action: "stop",
      active: true,
      info: ["10.0.0.9:1883"],
    });
    expect(second).toMatchObject({ label: "Video only", tag: "", detail: "", action: "start" });
  });

  it("opens a stream on its broker as the reference expands, else on its host and a head other than 1", () => {
    const infoOf = (items: ItemState[]) => {
      const [live] = sectionsOf({
        cameras: camerasOf(items),
        found: [],
        scanned: false,
        reported: new Map(),
        checks: new Map(),
        thumbnails: true,
        viewed: new Set<string>(),
        order: "address",
      });
      return live?.rows.flatMap((row) => row.children).map((row) => row.info);
    };
    // The owner's session reads `$BROKER` and `$CAM_HOST` as values, and `$UNSET` as nothing, which leaves the host.
    expect(infoOf(scenario("live-references").doc().workspace.items)).toEqual([
      ["198.51.100.2:1883"],
      ["10.0.0.5 · head 2"],
      ["10.0.0.6"],
    ]);
    // A session that reads no environment shows each reference as typed.
    expect(infoOf(scenario("live-typed").doc().workspace.items)).toEqual([
      ["$BROKER:1883"],
      ["$UNSET:1883"],
      ["$CAM_HOST"],
    ]);
  });
});

describe("found cameras the workspace holds", () => {
  it("are told by the host as entered and as a reference expands, whether or not the stream has resolved", () => {
    const items = scenario("live-found").doc().workspace.items;
    const found = ["10.0.0.5", "10.0.0.6", "10.0.0.7", "10.0.0.8"].map((host) => scanned(host));
    const [live, network] = sectionsOf({
      cameras: camerasOf(items),
      found,
      scanned: true,
      reported: new Map(),
      checks: new Map(),
      thumbnails: true,
      viewed: new Set<string>(),
      order: "address",
    });
    expect(live?.rows).toHaveLength(3);
    expect(live?.rows.map(({ aside }) => aside)).toEqual(["Q1656", "Q1656", "Q1656"]);
    expect(network?.rows.map(({ label }) => label)).toEqual(["10.0.0.7"]);
  });
});

describe("stream rows", () => {
  const refused = scanned("10.0.0.2", { sign_in: { state: "Refused" } });

  it("cannot start a stream whose camera refused its login, and the camera has failed", () => {
    const [live] = sectionsOf({
      cameras: camerasOf(WORKSPACE),
      found: [],
      scanned: false,
      reported: new Map(),
      checks: new Map([[B, refused]]),
      thumbnails: true,
      viewed: new Set([A]),
      order: "address",
    });
    const [camera] = live?.rows ?? [];
    const [open, stopped] = camera?.children ?? [];
    expect(camera).toMatchObject({
      aside: "Q1656",
      mark: { icon: "triangle-alert", color: "caution" },
    });
    // Open, a stream can still be stopped; stopped, it has no Start and no picture to hover.
    expect(open).toMatchObject({ action: "stop", thumbnail: `/api/live/snapshot/${A}` });
    expect(stopped).toMatchObject({
      action: null,
      detail: "The camera refused the login saved with it.",
      detailWarning: true,
      thumbnail: null,
      menu: ["information", "edit", "remove"],
    });
  });

  it("shows an open stream without a viewer as stopped, with Start to open it", () => {
    const [live] = sectionsOf({
      cameras: camerasOf(WORKSPACE),
      found: [],
      scanned: false,
      reported: new Map(),
      checks: new Map(),
      thumbnails: false,
      viewed: new Set(["other"]),
      order: "address",
    });
    const [first] = live?.rows[0]?.children ?? [];
    expect(first).toMatchObject({ action: "start", active: false, dim: true, detail: "" });
    expect(live?.rows[0]).toMatchObject({ aside: "Stopped", dim: true });
    // The header counts what the rows show as running.
    expect(streamingCount(camerasOf(WORKSPACE), new Set(["other"]))).toBe(0);
    expect(streamingCount(camerasOf(WORKSPACE), new Set([A]))).toBe(1);
  });

  it("lists streams that are resolving or failed, and offers what each can do", () => {
    const states = scenario("live-states");
    const { a, c, d } = states.ids;
    const content = states.doc().workspace.items.find((item) => item.id === c)?.resolution;
    // The recipe the resolving stream was read with before.
    const recipe =
      content?.status === "Resolved" && content.content.kind === "LiveVideo"
        ? content.content.recipe
        : null;
    const known = new Map(recipe ? [[c, recipe]] : []);
    const [live] = sectionsOf({
      cameras: camerasOf(states.doc("resolving").workspace.items, known),
      found: [],
      scanned: false,
      reported: new Map(),
      checks: new Map(),
      thumbnails: false,
      viewed: new Set<string>(),
      order: "address",
    });
    const rows = live?.rows.flatMap((camera) => camera.children) ?? [];
    expect(rows.map(({ id, action, menu, thumbnail }) => [id, action, menu, thumbnail])).toEqual([
      [a, "start", ["start", "information", "edit", "remove"], null],
      [c, null, ["edit", "remove"], null],
      [d, null, ["information", "edit", "remove"], null],
    ]);
    expect(rows[2]).toMatchObject({ detail: "Enter the device host.", detailWarning: true });
    // A stream that resolves again stays with its camera.
    expect(live?.rows).toHaveLength(2);
  });
});

describe("search and filter", () => {
  it("keeps the camera over a stream the search finds, and counts the rows the filter hides", () => {
    const found = [
      scanned("10.0.0.7", {
        identity: { model: "P3245", serial: "", firmware: "", product_type: "Dome" },
      }),
      scanned("10.0.0.8"),
    ];
    const all = sections(new Map(), found);
    const [live, network] = viewsOf(all, "scene", new Set());
    expect(
      live?.shown.map(({ row, children }) => [row.label, children.map((child) => child.row.label)]),
    ).toEqual([["Lobby", ["scene"]]]);
    expect(network?.listed).toBe(false);

    const hidden = new Set([facetKey({ group: "Type", value: "Dome" })]);
    const [, filtered] = viewsOf(all, "", hidden);
    expect(filtered).toMatchObject({ filteredOut: 1 });
    expect(filtered?.shown.map(({ row }) => row.label)).toEqual(["10.0.0.8"]);
    // A group is offered only with a choice to make, or once the user hid one of its values.
    expect(offeredFacets(all, new Set()).map(facetKey)).toEqual([
      facetKey({ group: "Type", value: "Box Camera" }),
      facetKey({ group: "Type", value: "Dome" }),
    ]);
  });
});

it("matches a search with case folded as the Workspace view does", () => {
  const found = [
    scanned("10.0.0.7", {
      identity: { model: "Straße", serial: "", firmware: "", product_type: "" },
    }),
  ];
  const [, network] = viewsOf(sections(new Map(), found), "STRASSE", new Set());
  expect(network?.shown.map(({ row }) => row.label)).toEqual(["10.0.0.7"]);
});

describe("stream status", () => {
  it("is the video's state, whatever the overlay does, and paused over any state while paused", () => {
    const live = { status: "Live" } as const;
    const failedOverlay = { overlay: { status: "Failed", reason: "SourceUnavailable" } } as const;
    const ready = { status: "ready", paused: false, ...failedOverlay } as const;
    expect(statusOf({ ...ready, video: live })).toMatchObject({ state: "live", anyLive: true });
    expect(statusOf({ ...ready, video: live, paused: true })).toMatchObject({
      state: "paused",
      anyLive: false,
    });
    const failed = { status: "Failed", reason: "LoginRejected" } as const;
    expect(statusOf({ ...ready, video: failed })).toMatchObject({
      state: "failed",
      text: "Login rejected",
    });
    expect(statusOf({ ...ready, video: failed, paused: true })).toMatchObject({ state: "paused" });
  });

  it("is the session's failure or reconnecting, not the Connecting its feeds fall back to", () => {
    const feeds = { video: { status: "Connecting" }, overlay: null, paused: false } as const;
    expect(
      statusOf({ ...feeds, status: "failed", failure: { reason: "The backend ended the stream" } }),
    ).toMatchObject({ state: "failed", text: "The backend ended the stream" });
    expect(statusOf({ ...feeds, status: "reconnecting", failure: undefined })).toMatchObject({
      state: "reconnecting",
      text: "Reconnecting",
    });
  });
});
