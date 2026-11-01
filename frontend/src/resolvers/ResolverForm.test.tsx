import { afterEach, describe, expect, it, vi } from "vite-plus/test";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { DecoderInfo, ResolverInfo } from "../generated/api";
import type { WindowBridge } from "../shell/windowBridge";
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
  ],
};
const DECODERS: DecoderInfo[] = [
  { handler: "MOT_FILE", display_name: "MOT Annotations", extensions: [".txt"] },
];

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

function Harness({ initial }: { initial?: Settings }) {
  const form = useResolverForm(MOT, DECODERS, initial);
  return (
    <>
      <ResolverForm form={form} refusal={null} />
      <div data-testid="settings">{JSON.stringify(form.settings)}</div>
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
  it("loads only what is checked, and leaves the key out while everything is", async () => {
    const scans = stubScans();
    render(<Harness />);
    expect(screen.getByRole("button", { name: "Scan" })).toHaveProperty("disabled", true);
    type("/data");
    scan();
    expect(scans[0].body).toEqual({ field: "sequences", settings: { root: "/data" } });
    expect(screen.getByRole("status").textContent).toBe("Scanning…");
    expect(settings()).toBeNull();
    await respond(scans, 0, "SEQ-01", "SEQ-02");
    expect(settings()).toEqual({ root: "/data" });

    fireEvent.click(screen.getByRole("checkbox", { name: /SEQ-02/ }));
    expect(settings()).toEqual({ root: "/data", sequences: ["SEQ-01"] });
    fireEvent.click(screen.getByRole("checkbox", { name: /SEQ-01/ }));
    expect(settings()).toBeNull();
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
    await act(async () => scans[0].answer(422, { reason: "No such folder", location: "root" }));
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
    render(<Harness />);
    fireEvent.click(screen.getByRole("button", { name: "Browse Dataset folder" }));
    await waitFor(() => expect(scans).toHaveLength(1));
    expect(scans[0].body.settings).toEqual({ root: "/picked" });
    expect((screen.getByLabelText("Dataset folder") as HTMLInputElement).value).toBe("/picked");
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
});
