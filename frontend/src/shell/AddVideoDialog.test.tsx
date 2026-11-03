import { afterEach, beforeEach, expect, it, vi } from "vite-plus/test";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import type { DecoderInfo } from "../generated/api";
import { FakeSocket } from "../session/fakeSession";
import { SessionProvider } from "../session/session";
import { AddVideoDialog, decodersFor } from "./AddVideoDialog";

const decoder = (handler: string, ...extensions: string[]): DecoderInfo => ({
  handler,
  display_name: handler,
  extensions,
  options: [],
});

beforeEach(() => vi.stubGlobal("WebSocket", FakeSocket));
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  window.sessionStorage.clear();
  FakeSocket.reset();
});

it("picks the decoders whose extension ends the file name, however many dots it holds", () => {
  const decoders = [
    decoder("JSONL", ".jsonl"),
    decoder("ACME", ".acme.jsonl"),
    decoder("MOT", ".txt"),
  ];
  const handlers = (path: string) => decodersFor(decoders, path).map((one) => one.handler);

  expect(handlers("/data/run.ACME.jsonl")).toEqual(["JSONL", "ACME"]);
  expect(handlers("C:\\data\\gt.txt")).toEqual(["MOT"]);
  expect(handlers("/data/notes.mp3")).toEqual(["JSONL", "ACME", "MOT"]);
});

const reply = (status: number, json: unknown) => ({
  ok: status < 400,
  status,
  json: async () => json,
});
const open = async () => {
  render(
    <SessionProvider>
      <AddVideoDialog onClose={() => {}} />
    </SessionProvider>,
  );
  fireEvent.change(await screen.findByLabelText("Path on the server"), {
    target: { value: "/a.mp4" },
  });
};
const overlay = async (path: string) =>
  fireEvent.change(await screen.findByLabelText("Overlay path"), { target: { value: path } });
const add = () => screen.getByRole("button", { name: "Add" });

it("chooses a handler for the user only when one reads the file, else waits for the choice", async () => {
  const decoders = [
    decoder("V1", ".jsonl"),
    decoder("V2", ".jsonl"),
    decoder("X1", ".xml"),
    decoder("X2", ".xml"),
    decoder("MOT", ".txt"),
  ];
  vi.stubGlobal("fetch", async () => reply(200, { decoders, resolvers: [] }));
  await open();

  await overlay("/data/gt.txt");
  expect(screen.queryByLabelText("Data handler")).toBeNull();
  expect(add()).toHaveProperty("disabled", false);

  await overlay("/data/v1.jsonl");
  const handler = screen.getByLabelText("Data handler") as HTMLSelectElement;
  expect(handler.value).toBe("");
  expect(screen.getByRole("status").textContent).toBe(
    "Choose the data handler that reads v1.jsonl.",
  );
  expect(add()).toHaveProperty("disabled", true);

  fireEvent.change(handler, { target: { value: "V2" } });
  expect(screen.queryByRole("status")).toBeNull();
  expect(add()).toHaveProperty("disabled", false);

  // A pick that no longer reads the file is dropped, not replaced by another handler.
  await overlay("/data/c.xml");
  expect((screen.getByLabelText("Data handler") as HTMLSelectElement).value).toBe("");
  expect(add()).toHaveProperty("disabled", true);

  // ... and does not come back for a later file it could read.
  await overlay("/data/other.jsonl");
  expect((screen.getByLabelText("Data handler") as HTMLSelectElement).value).toBe("");
  expect(add()).toHaveProperty("disabled", true);
});

it("says why the handlers could not be loaded and asks again on Retry", async () => {
  vi.stubGlobal("fetch", async () =>
    reply(403, { code: "Internal", params: {}, reason: "Handlers unavailable" }),
  );
  await open();
  expect((await screen.findByRole("alert")).textContent).toContain("Handlers unavailable");
  vi.stubGlobal("fetch", async () => reply(200, { decoders: [], resolvers: [] }));
  fireEvent.click(screen.getByRole("button", { name: "Retry" }));
  expect(await screen.findByLabelText("Overlay path")).toBeTruthy();
  expect(screen.queryByRole("alert")).toBeNull();
});
