import { useState } from "react";
import { afterEach, describe, expect, it, vi } from "vite-plus/test";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { GrantsContext } from "../access";
import type { DecoderInfo, ResolverInfo } from "../generated/api";
import type { WindowBridge } from "../windowBridge";
import { ResolverForm, useResolverForm } from "./ResolverForm";
import type { Settings } from "./scan";

const MOT: ResolverInfo = {
  id: "mot_challenge",
  display_name: "MOT Challenge",
  description: "",
  fields: [
    { key: "root", label: "Dataset folder", kind: { kind: "Folder" }, required: true },
    {
      key: "sequences",
      label: "Sequences",
      kind: { kind: "Scan", depends_on: ["root"] },
      required: false,
    },
    { key: "stride", label: "Stride", kind: { kind: "Integer", min: 1n }, required: false },
  ],
};
const DECODERS: DecoderInfo[] = [
  {
    handler: "MOT_FILE",
    display_name: "MOT Annotations",
    extensions: [".txt"],
    live: true,
    options: [
      {
        key: "width",
        label: "Image width",
        kind: { kind: "Integer", min: 1n },
        required: true,
        default: 1920,
      },
    ],
  },
];
const PAIR: ResolverInfo = {
  id: "pair",
  display_name: "Pair",
  description: "",
  fields: [
    { key: "format", label: "Format", kind: { kind: "Handler" }, required: true },
    {
      key: "options",
      label: "Options",
      kind: { kind: "DecoderOptions", handler_field: "format" },
      required: false,
    },
    { key: "scale", label: "Scale", kind: { kind: "Text" }, required: true, default: "1" },
  ],
};

const DEPTH: ResolverInfo = {
  id: "depth",
  display_name: "Depth",
  description: "",
  fields: [
    { key: "format", label: "Format", kind: { kind: "Handler" }, required: true },
    {
      key: "options",
      label: "Options",
      kind: { kind: "DecoderOptions", handler_field: "format" },
      required: true,
    },
    {
      key: "depth",
      label: "Depth",
      kind: { kind: "Integer", min: 1n },
      required: false,
      default: 1,
    },
    {
      key: "sequences",
      label: "Sequences",
      kind: { kind: "Scan", depends_on: ["depth"] },
      required: false,
    },
  ],
};

const COUNT: ResolverInfo = {
  id: "count",
  display_name: "Count",
  description: "",
  fields: [
    { key: "videos", label: "Videos", kind: { kind: "Folder" }, required: true },
    { key: "format", label: "Format", kind: { kind: "Handler" }, required: true },
    {
      key: "pairs",
      label: "Pairs",
      kind: {
        kind: "Scan",
        depends_on: ["videos"],
        count: { missing: "Pick videos.", found: "Found {count} of {count}.", none: "None found." },
      },
      required: false,
    },
  ],
};

const BOTH: ResolverInfo = {
  ...COUNT,
  id: "both",
  fields: [
    ...COUNT.fields,
    {
      key: "sequences",
      label: "Sequences",
      kind: { kind: "Scan", depends_on: ["videos"] },
      required: false,
    },
  ],
};

interface Scan {
  body: { field: string; settings: Settings };
  signal: AbortSignal;
  answer: (status: number, json: unknown) => void;
}

/** A backend whose scans wait until the test answers them; an abort rejects them unless `ignoreAbort`. */
function stubScans(ignoreAbort = false) {
  const scans: Scan[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(
      (_url: string, init: RequestInit) =>
        new Promise((resolve, reject) => {
          const signal = init.signal as AbortSignal;
          if (!ignoreAbort) {
            signal.addEventListener("abort", () =>
              reject(new DOMException("The operation was aborted", "AbortError")),
            );
          }
          scans.push({
            body: JSON.parse(init.body as string),
            signal,
            answer: (status, json) => resolve({ ok: status < 400, status, json: async () => json }),
          });
        }),
    ),
  );
  return scans;
}

const found = (...names: string[]) =>
  names.map((name) => ({ value: name, label: name, detail: "64×48 · 25 fps" }));

function Harness({ initial, resolver = MOT }: { initial?: Settings; resolver?: ResolverInfo }) {
  const form = useResolverForm(resolver, DECODERS, initial);
  const [after, setAfter] = useState("unset");
  return (
    <>
      <ResolverForm form={form} />
      <div data-testid="settings">{JSON.stringify(form.settings)}</div>
      <button
        disabled={!form.settingsAfterScan}
        onClick={() => void form.settingsAfterScan?.().then((s) => setAfter(JSON.stringify(s)))}
      >
        After
      </button>
      <div data-testid="after">{after}</div>
    </>
  );
}

const settings = () => JSON.parse(screen.getByTestId("settings").textContent ?? "null");
const type = (text: string) =>
  fireEvent.change(screen.getByLabelText("Dataset folder"), { target: { value: text } });
const scan = () => fireEvent.click(screen.getByRole("button", { name: "Scan" }));
const respond = (scans: Scan[], index: number, ...names: string[]) =>
  act(async () => scans[index].answer(200, found(...names)));

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  delete window.axDevilWindow;
});

describe("resolver form", () => {
  it("loads exactly what is checked, naming every sequence of a new form", async () => {
    const scans = stubScans();
    // An id that is also an inherited property name must not find a form that was never kept.
    render(<Harness resolver={{ ...MOT, id: "constructor" }} />);
    expect(screen.getByRole("button", { name: "Scan" })).toHaveProperty("disabled", true);
    type("/data");
    fireEvent.change(screen.getByLabelText("Stride"), { target: { value: "2" } });
    scan();
    expect(scans[0].body).toEqual({ field: "sequences", settings: { root: "/data", stride: 2 } });
    expect(screen.getByRole("status").textContent).toBe("Scanning…");
    expect(settings()).toBeNull();
    await respond(scans, 0, "SEQ-01", "SEQ-02");
    expect(settings()).toEqual({ root: "/data", stride: 2, sequences: ["SEQ-01", "SEQ-02"] });

    fireEvent.click(screen.getByRole("checkbox", { name: /SEQ-02/ }));
    expect(settings()).toEqual({ root: "/data", stride: 2, sequences: ["SEQ-01"] });
    // Nothing checked still loads; the dialog says why it does not go on.
    fireEvent.click(screen.getByRole("checkbox", { name: /SEQ-01/ }));
    expect(settings()).toEqual({ root: "/data", stride: 2, sequences: [] });
  });

  it("empties the list when the root is edited, and stops the scan it started", async () => {
    const scans = stubScans();
    render(<Harness />);
    type("/data");
    scan();
    type("/other");
    expect(scans[0].signal.aborted).toBe(true);
    expect(screen.queryByRole("status")).toBeNull();
    expect(settings()).toBeNull();

    scan();
    await respond(scans, 1, "SEQ-09");
    expect(screen.getByRole("checkbox", { name: /SEQ-09/ })).toBeTruthy();
    type("/third");
    expect(screen.queryByRole("group", { name: "Sequences" })).toBeNull();
    expect(settings()).toBeNull();
  });

  it("lets only the newest scan fill the list, even when an older one answers last", async () => {
    const scans = stubScans(true);
    render(<Harness />);
    type("/data");
    scan();
    scan();
    await respond(scans, 1, "NEW");
    await respond(scans, 0, "OLD");
    expect(screen.getByRole("checkbox", { name: /NEW/ })).toBeTruthy();
    expect(screen.queryByRole("checkbox", { name: /OLD/ })).toBeNull();
  });

  it("shows a refused scan at the field it names, and a cancelled one shows nothing", async () => {
    const scans = stubScans();
    render(<Harness />);
    type("/gone");
    scan();
    await act(async () =>
      scans[0].answer(422, {
        code: "PlaylistInvalid",
        params: {},
        reason: "No such folder",
        location: "root",
      }),
    );
    const alert = screen.getByRole("alert");
    expect(alert.textContent).toContain("No such folder");
    expect(alert.parentElement?.contains(screen.getByLabelText("Dataset folder"))).toBe(true);
    expect(settings()).toBeNull();

    scan();
    scan(); // the first of these is cancelled by the second
    await act(async () => Promise.resolve());
    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.getByRole("status").textContent).toBe("Scanning…");
  });

  it("stops a running scan when the form goes away", () => {
    const scans = stubScans();
    const { unmount } = render(<Harness />);
    type("/data");
    scan();
    unmount();
    expect(scans[0].signal.aborted).toBe(true);
  });

  it("scans at once when a folder is picked", async () => {
    const scans = stubScans();
    window.axDevilWindow = { pickFolder: () => Promise.resolve("/picked") } as WindowBridge;
    render(
      <GrantsContext.Provider value={["preferences"]}>
        <Harness />
      </GrantsContext.Provider>,
    );
    fireEvent.click(screen.getByRole("button", { name: "Browse Dataset folder" }));
    await waitFor(() => expect(scans).toHaveLength(1));
    expect(scans[0].body.settings).toEqual({ root: "/picked" });
    expect((screen.getByLabelText("Dataset folder") as HTMLInputElement).value).toBe("/picked");
  });

  it("offers Browse only in the backend's own window, not in a window on another machine's server", () => {
    window.axDevilWindow = { pickFolder: () => Promise.resolve("/picked") } as WindowBridge;
    render(<Harness />);
    expect(screen.queryByRole("button", { name: "Browse Dataset folder" })).toBeNull();
  });

  it("restores saved settings: scans, keeps unknown keys, and marks saved names that are gone", async () => {
    const scans = stubScans();
    render(<Harness initial={{ root: "/data", sequences: ["B", "gone"], extra: 1 }} />);
    await waitFor(() => expect(scans).toHaveLength(1));
    await respond(scans, 0, "A", "B");
    const gone = screen.getByRole("checkbox", { name: /gone/ });
    expect((gone as HTMLInputElement).checked).toBe(true);
    expect(gone.closest("label")?.textContent).toContain("Not found");
    expect(settings()).toEqual({ root: "/data", sequences: ["B", "gone"], extra: 1 });
  });

  it("starts a required field at its default, shows it while blank, and refuses an integer that is no whole number", () => {
    render(<Harness resolver={PAIR} />);
    // A decoder is never chosen for the user.
    expect((screen.getByLabelText("Format") as HTMLSelectElement).value).toBe("");
    expect(screen.queryByLabelText("Image width")).toBeNull();
    expect(settings()).toBeNull();
    fireEvent.change(screen.getByLabelText("Format"), { target: { value: "MOT_FILE" } });
    const width = screen.getByLabelText("Image width") as HTMLInputElement;
    expect(width.value).toBe("1920");
    expect((screen.getByLabelText("Scale") as HTMLInputElement).value).toBe("1");
    expect(settings()).toEqual({ format: "MOT_FILE", options: { width: 1920 }, scale: "1" });

    fireEvent.change(width, { target: { value: "" } });
    fireEvent.change(screen.getByLabelText("Scale"), { target: { value: "" } });
    expect(settings()).toEqual({ format: "MOT_FILE" });
    expect(width.placeholder).toBe("1920");

    fireEvent.change(width, { target: { value: "640px" } });
    expect(settings()).toBeNull();
    expect(screen.getByRole("alert").textContent).toBe("Whole number");
    fireEvent.change(width, { target: { value: "640" } });
    expect(settings()).toEqual({ format: "MOT_FILE", options: { width: 640 } });
  });

  it("scans with the values resolution would use: defaults, integers and decoder option maps", () => {
    const scans = stubScans();
    render(<Harness resolver={DEPTH} />);
    fireEvent.change(screen.getByLabelText("Format"), { target: { value: "MOT_FILE" } });
    expect(screen.getByRole("button", { name: "Scan" })).toHaveProperty("disabled", false);
    fireEvent.change(screen.getByLabelText("Image width"), { target: { value: "640" } });
    scan();
    expect(scans[0].body.settings).toEqual({
      format: "MOT_FILE",
      options: { width: 640 },
      depth: 1,
    });
    // A required option map with nothing in it is still sent, as an empty map.
    fireEvent.change(screen.getByLabelText("Image width"), { target: { value: "" } });
    scan();
    expect(scans[1].body.settings).toEqual({ format: "MOT_FILE", options: {}, depth: 1 });
    fireEvent.change(screen.getByLabelText("Depth"), { target: { value: "" } });
    expect(screen.getByRole("button", { name: "Scan" })).toHaveProperty("disabled", false);
    fireEvent.change(screen.getByLabelText("Depth"), { target: { value: "x" } });
    expect(screen.getByRole("button", { name: "Scan" })).toHaveProperty("disabled", true);
  });

  it("counts pairs without gating Load on a scan, and goes on after one only if the settings are as asked", async () => {
    const scans = stubScans();
    render(<Harness resolver={COUNT} />);
    // Scan stays enabled; with nothing to scan it says what is missing and asks nothing.
    expect(screen.getByRole("status").textContent).toBe("Pick videos.");
    scan();
    expect(scans).toHaveLength(0);
    // A folder is sent without the spaces around it.
    fireEvent.change(screen.getByLabelText("Videos"), { target: { value: " /v " } });
    fireEvent.change(screen.getByLabelText("Format"), { target: { value: "MOT_FILE" } });
    expect(scans).toHaveLength(0);
    expect(screen.queryByRole("status")).toBeNull();
    expect(settings()).toEqual({ videos: "/v", format: "MOT_FILE" });

    // An empty answer blocks Load; a failure says so and blocks it too.
    scan();
    expect(settings()).toBeNull();
    await respond(scans, 0);
    expect(screen.getByRole("status").textContent).toBe("None found.");
    expect(settings()).toBeNull();
    scan();
    await act(async () =>
      scans[1].answer(422, { code: "PlaylistInvalid", params: {}, reason: "No such folder" }),
    );
    expect(screen.getByRole("alert").textContent).toContain("Scan failed: No such folder");
    expect(settings()).toBeNull();

    // The count survives a new decoder; a new folder forgets it.
    scan();
    await respond(scans, 2, "a", "b");
    expect(screen.getByRole("status").textContent).toBe("Found 2 of 2.");
    fireEvent.change(screen.getByLabelText("Format"), { target: { value: "" } });
    fireEvent.change(screen.getByLabelText("Format"), { target: { value: "MOT_FILE" } });
    expect(screen.getByRole("status").textContent).toBe("Found 2 of 2.");
    expect(settings()).toEqual({ videos: "/v", format: "MOT_FILE" });
    expect(screen.getByRole("button", { name: "After" })).toHaveProperty("disabled", true);
    fireEvent.change(screen.getByLabelText("Videos"), { target: { value: "/w" } });
    expect(screen.queryByRole("status")).toBeNull();

    // Loading before any scan scans first, and goes on only if the settings are the same when it answers.
    fireEvent.click(screen.getByRole("button", { name: "After" }));
    await waitFor(() => expect(scans).toHaveLength(4));
    await respond(scans, 3, "c");
    await waitFor(() =>
      expect(screen.getByTestId("after").textContent).toBe('{"videos":"/w","format":"MOT_FILE"}'),
    );
    fireEvent.change(screen.getByLabelText("Videos"), { target: { value: "/x" } });
    fireEvent.click(screen.getByRole("button", { name: "After" }));
    await waitFor(() => expect(scans).toHaveLength(5));
    fireEvent.change(screen.getByLabelText("Format"), { target: { value: "" } });
    await respond(scans, 4, "d");
    await waitFor(() => expect(screen.getByTestId("after").textContent).toBe("null"));

    // A decoder changed away and back is the decoder that was asked for.
    fireEvent.change(screen.getByLabelText("Videos"), { target: { value: "/y" } });
    fireEvent.change(screen.getByLabelText("Format"), { target: { value: "MOT_FILE" } });
    fireEvent.click(screen.getByRole("button", { name: "After" }));
    await waitFor(() => expect(scans).toHaveLength(6));
    fireEvent.change(screen.getByLabelText("Format"), { target: { value: "" } });
    fireEvent.change(screen.getByLabelText("Format"), { target: { value: "MOT_FILE" } });
    await respond(scans, 5, "e");
    await waitFor(() =>
      expect(screen.getByTestId("after").textContent).toBe('{"videos":"/y","format":"MOT_FILE"}'),
    );
  });

  it("does not load what was unchecked while the pairs were being counted", async () => {
    const scans = stubScans();
    render(<Harness resolver={BOTH} initial={{ videos: "/v", format: "MOT_FILE" }} />);
    await respond(scans, 0, "a", "b");
    fireEvent.click(screen.getByRole("button", { name: "After" }));
    await waitFor(() => expect(scans).toHaveLength(2));
    fireEvent.click(screen.getByRole("checkbox", { name: /b/ }));
    await respond(scans, 1, "p");
    await waitFor(() => expect(screen.getByTestId("after").textContent).toBe("null"));
  });
});
