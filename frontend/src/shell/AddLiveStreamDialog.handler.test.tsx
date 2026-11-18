import { afterEach, beforeEach, expect, it, vi } from "vite-plus/test";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { DEFAULT_SETTINGS, FakeSettings } from "../client/fakeSettings";
import { FakeSocket } from "../session/fakeSession";
import { SessionProvider } from "../session/session";
import { AddLiveStreamDialog } from "./AddLiveStreamDialog";
import { waits } from "./dialogTesting";

const HANDLERS = {
  decoders: [
    {
      handler: "ADF_V1_FRAME",
      display_name: "ADF v1 Frame",
      extensions: [],
      options: [],
      live: true,
    },
    { handler: "ONVIF_XML", display_name: "ONVIF XML", extensions: [], options: [], live: true },
    { handler: "FILE", display_name: "File only", extensions: [], options: [], live: false },
  ],
  resolvers: [],
};
let release = () => {};
beforeEach(() => {
  const handlers = new Promise<void>((resolve) => (release = resolve));
  vi.stubGlobal("WebSocket", FakeSocket);
  vi.stubGlobal("fetch", async (url: string) => {
    if (url === "/api/handlers") {
      await handlers;
      return Response.json(HANDLERS);
    }
    return Response.json({ listed: false, common: [], heads: [], topics: [], sources: [] });
  });
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  window.sessionStorage.clear();
  FakeSocket.reset();
});

async function open(saved?: string) {
  render(
    <FakeSettings state={DEFAULT_SETTINGS}>
      <SessionProvider>
        <AddLiveStreamDialog
          editing={saved === undefined ? undefined : "item-1"}
          initial={saved === undefined ? undefined : { overlay: { mode: "rtsp", handler: saved } }}
          onClose={() => {}}
        />
      </SessionProvider>
    </FakeSettings>,
  );
  await screen.findByLabelText("Overlay mode");
}
const pick = (label: string, value: string) =>
  fireEvent.change(screen.getByLabelText(label), { target: { value } });
const value = (label: string) => (screen.getByLabelText(label) as HTMLSelectElement).value;
const discovered = async () => {
  await act(async () => release());
  await screen.findByRole("option", { name: "ADF v1 Frame" });
};

const orders = ["discovery first", "mode first", "no mode edit"] as const;
const dialogs = [
  { dialog: "Add", saved: undefined, handler: "ADF_V1_FRAME" },
  { dialog: "Edit available", saved: "ONVIF_XML", handler: "ONVIF_XML" },
  { dialog: "Edit unavailable", saved: "GONE_V1", handler: "GONE_V1" },
];
it.each(dialogs.flatMap((dialog) => orders.map((order) => ({ ...dialog, order }))))(
  "$dialog retains its initial shared handler with $order",
  async ({ saved, handler, order }) => {
    await open(saved);
    if (order === "discovery first") await discovered();
    if (order !== "no mode edit") pick("Overlay mode", "mqtt");
    if (order !== "discovery first") await discovered();
    expect(value("Handler type")).toBe(handler);
    expect(value("Overlay mode")).toBe(
      order === "no mode edit" ? (saved === undefined ? "datahub" : "rtsp") : "mqtt",
    );
    if (handler === "GONE_V1")
      expect(screen.getByRole("option", { name: "GONE_V1 (unavailable)" })).toBeTruthy();
    expect(screen.queryByRole("option", { name: "File only" })).toBeNull();
  },
);

it("keeps an explicit None chosen while discovery was pending", async () => {
  await open();
  pick("Overlay mode", "rtsp");
  pick("Handler type", "");
  await discovered();
  expect(value("Handler type")).toBe("");
  expect(waits(screen.getByRole("button", { name: "Add" }))).toBe(true);
});

it.each(["", "ONVIF_XML"])(
  "keeps an explicit handler '%s' across mode switches",
  async (handler) => {
    await open();
    await discovered();
    pick("Handler type", handler);
    pick("Overlay mode", "none");
    expect((screen.getByLabelText("Handler type") as HTMLSelectElement).disabled).toBe(true);
    pick("Overlay mode", "rtsp");
    expect(value("Handler type")).toBe(handler);
  },
);
