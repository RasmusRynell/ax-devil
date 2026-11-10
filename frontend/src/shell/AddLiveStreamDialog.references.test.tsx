import { afterEach, beforeEach, expect, it, vi } from "vite-plus/test";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { AccessContext } from "../access";
import { DEFAULT_SETTINGS, FakeSettings } from "../fakeSettings";
import { FakeSocket } from "../session/fakeSession";
import { SessionProvider } from "../session/session";
import { AddLiveStreamDialog } from "./AddLiveStreamDialog";

const REFERENCES = {
  ...DEFAULT_SETTINGS,
  values: {
    ...DEFAULT_SETTINGS.values,
    "camera.host": "$AX_DEVIL_TARGET_ADDR",
    "camera.user": "$AX_DEVIL_TARGET_USER",
    "camera.password": "$AX_DEVIL_TARGET_PASS",
  },
};

/** What the backend answers the form's question about the defaults' references, and the question as it came. */
async function show(answer: object) {
  const asked = vi.fn();
  vi.stubGlobal("fetch", async (url: string, init?: RequestInit) => {
    if (url === "/api/live/expansions") {
      asked(init?.body);
      return { ok: true, status: 200, json: async () => answer };
    }
    return { ok: true, status: 200, json: async () => ({ decoders: [], resolvers: [] }) };
  });
  render(
    <AccessContext value={{ owner: true, fullAccess: true }}>
      <FakeSettings state={REFERENCES}>
        <SessionProvider>
          <AddLiveStreamDialog onClose={() => {}} />
        </SessionProvider>
      </FakeSettings>
    </AccessContext>,
  );
  return asked;
}

const placeholder = (label: string) =>
  (screen.getByLabelText(label) as HTMLInputElement).placeholder;

beforeEach(() => vi.stubGlobal("WebSocket", FakeSocket));
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  window.sessionStorage.clear();
  FakeSocket.reset();
});

it("shows the value of a default's reference and the variable it comes from, and a password only as dots", async () => {
  const asked = await show({
    host: "172.20.1.1",
    username: "root",
    password_set: true,
    broker_host: null,
    broker_user: null,
    broker_pass_set: null,
  });
  await waitFor(() => expect(placeholder("Host")).toBe("172.20.1.1  (via $AX_DEVIL_TARGET_ADDR)"));
  expect(placeholder("Username")).toBe("root  (via $AX_DEVIL_TARGET_USER)");
  expect(placeholder("Password")).toBe("••••••••  (via $AX_DEVIL_TARGET_PASS)");
  expect(placeholder("Name")).toBe("172.20.1.1");
  // What the backend may not expand stays the reference.
  expect(placeholder("MQTT Host")).not.toContain("via");
  expect(JSON.parse(asked.mock.calls[0]?.[0] as string)).toEqual({});
});

it("keeps the reference where the backend does not expand it, and shows nothing where the variable is not set", async () => {
  await show({
    host: null,
    username: "",
    password_set: false,
    broker_host: null,
    broker_user: null,
    broker_pass_set: null,
  });
  await waitFor(() => expect(placeholder("Name")).toBe("Live stream"));
  expect(placeholder("Host")).toBe("$AX_DEVIL_TARGET_ADDR");
  expect(placeholder("Username")).toBe("");
  expect(placeholder("Password")).toBe("");
});
