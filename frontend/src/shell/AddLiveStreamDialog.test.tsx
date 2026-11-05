import { afterEach, beforeEach, describe, expect, it, vi } from "vite-plus/test";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { GrantsContext } from "../access";
import { DEFAULT_SETTINGS, FakeSettings } from "../fakeSettings";
import { FakeSocket } from "../session/fakeSession";
import { SessionProvider } from "../session/session";
import { AddLiveStreamDialog } from "./AddLiveStreamDialog";

const HANDLERS = {
  decoders: [
    {
      handler: "ADF_V1_FRAME",
      display_name: "ADF v1 Frame",
      extensions: [],
      options: [],
      live: true,
    },
    { handler: "ACME", display_name: "Acme file", extensions: [".acme"], options: [], live: false },
  ],
  resolvers: [],
};

/** The overlay of each question put to the camera for its sources or topics. */
let listings: string[] = [];
/** The connection of each question put to the camera. */
let asked: { like?: string }[] = [];
/** While set, the camera's lists wait for it, as a slow camera would. */
let gate: Promise<void> | null = null;
/** Called when the form reads the body of a camera's sources answer. */
let onRead = () => {};

beforeEach(() => {
  listings = [];
  asked = [];
  gate = null;
  onRead = () => {};
  vi.stubGlobal("WebSocket", FakeSocket);
  vi.stubGlobal("fetch", async (url: string, init?: RequestInit) => {
    if (url === "/api/live/sources") listings.push(JSON.parse(init?.body as string).overlay);
    if (url.startsWith("/api/live/")) asked.push(JSON.parse(init?.body as string).connection);
    if (url === "/api/live/sources") await gate;
    const json =
      url === "/api/handlers"
        ? HANDLERS
        : url === "/api/live/resolutions"
          ? { resolutions: [{ width: 1280, height: 720 }] }
          : JSON.parse(init?.body as string).overlay === "mqtt"
            ? { overlay: "mqtt", sources: ["com.axis.scene.frame.v1#1"] }
            : { overlay: "datahub", topics: ["com.axis.scene.frame.v1"] };
    return {
      ok: true,
      status: 200,
      json: async () => {
        if (url === "/api/live/sources") onRead();
        return json;
      },
    };
  });
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  window.sessionStorage.clear();
  FakeSocket.reset();
});

/** Settings that configure no camera host, so the form has to be given one. */
const NO_HOST = {
  ...DEFAULT_SETTINGS,
  values: { ...DEFAULT_SETTINGS.values, "camera.host": "" },
};

const open = async (onClose = () => {}, state = DEFAULT_SETTINGS, seed = {}) => {
  render(
    <FakeSettings state={state}>
      <SessionProvider>
        <AddLiveStreamDialog seed={seed} onClose={onClose} />
      </SessionProvider>
    </FakeSettings>,
  );
  // The decoders arrive, and the form takes the overlay the settings choose.
  await waitFor(() =>
    expect((screen.getByLabelText("Overlay Mode") as HTMLSelectElement).value).toBe("datahub"),
  );
};
const type = (label: string, value: string) =>
  fireEvent.change(screen.getByLabelText(label), { target: { value } });
const ok = () => screen.getByRole("button", { name: "OK" }) as HTMLButtonElement;
const refresh = (label: string) =>
  fireEvent.click(screen.getByLabelText(label).closest("div")?.querySelector("button") as Element);
/** The camera has listed the topics, and the configured one is chosen. */
const listed = () => waitFor(() => expect(ok().disabled).toBe(false));
const commands = () => FakeSocket.latest.received.filter((message) => message.type === "command");

describe("add live stream dialog", () => {
  it("is one page in the Python app's order, with OK waiting for a host and saying why", async () => {
    await open(() => {}, NO_HOST);
    const labels = [...document.querySelectorAll("form label")].map((label) => label.textContent);
    expect(labels.slice(0, 10)).toEqual([
      "Name",
      "Host",
      "Username",
      "Password",
      "Protocol",
      "Port",
      "Camera Head",
      "RTSP Port",
      "Resolution",
      "Custom Stream URL",
    ]);
    expect(ok().disabled).toBe(true);
    expect(screen.getByRole("alert").textContent).toBe("Enter the device host.");
    type("Host", "10.0.0.7");
    // A changed host is a new camera: its topics are read on Refresh.
    refresh("DataHub Topic");
    await listed();
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("keeps what was typed for each overlay while another is chosen, and adds only the chosen one", async () => {
    const onClose = vi.fn();
    await open(onClose);
    await listed();
    type("Overlay Mode", "mqtt");
    type("MQTT Host", "broker");
    await waitFor(() =>
      expect(screen.getByRole("option", { name: "com.axis.scene.frame.v1#1" })).toBeTruthy(),
    );
    type("Data Source", "com.axis.scene.frame.v1#1");
    type("Overlay Mode", "datahub");
    type("Topic Channel", "3");
    type("Overlay Mode", "mqtt");
    expect((screen.getByLabelText("MQTT Host") as HTMLInputElement).value).toBe("broker");
    // Each mode's list stays while the connection does.
    expect(listings).toEqual(["datahub", "mqtt"]);

    fireEvent.click(ok());
    await waitFor(() => expect(onClose).toHaveBeenCalled());
    expect(commands()).toMatchObject([
      {
        cmd: {
          command: {
            type: "addItems",
            items: [
              {
                type: "Live",
                overlay: {
                  mode: "mqtt",
                  handler: "ADF_V1_FRAME",
                  broker_host: "broker",
                  source_key: "com.axis.scene.frame.v1#1",
                },
              },
            ],
            open: true,
          },
        },
      },
    ]);
    expect(JSON.stringify(commands())).not.toContain("channel");
  });

  it("asks the camera as the stream will be added, naming the stream it is another of", async () => {
    await open(() => {}, DEFAULT_SETTINGS, { like: "item-1" });
    await listed();
    type("Overlay Mode", "mqtt");
    // Sizes and both lists: the backend authenticates them with that stream's saved login.
    await waitFor(() => expect(asked.length).toBeGreaterThanOrEqual(3));
    expect(asked.every((connection) => connection.like === "item-1")).toBe(true);
  });

  it("needs a handler type for an overlay mode, offering only decoders that can read a live stream", async () => {
    await open();
    expect(screen.getByRole("option", { name: "ADF v1 Frame" })).toBeTruthy();
    expect(screen.queryByRole("option", { name: "Acme file" })).toBeNull();
    type("Handler Type", "");
    expect(ok().disabled).toBe(true);
    expect(screen.getByRole("alert").textContent).toBe(
      "Select a handler type for DataHub WebSocket overlays.",
    );
    type("Overlay Mode", "none");
    expect((screen.getByLabelText("Handler Type") as HTMLSelectElement).disabled).toBe(true);
    expect(ok().disabled).toBe(false);
  });

  it("shows the backend's refusal under the form and keeps OK off until the form changes", async () => {
    const onClose = vi.fn();
    await open(onClose);
    await listed();
    FakeSocket.failContent = {
      code: "WorkspaceInvalid",
      params: {},
      reason: "Camera Head must be a positive integer.",
      location: "head",
    };
    fireEvent.click(ok());
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toBe("Camera Head must be a positive integer.");
    expect(ok().disabled).toBe(true);
    expect(onClose).not.toHaveBeenCalled();

    type("Camera Head", "2");
    expect(screen.queryByRole("alert")).toBeNull();
    expect(ok().disabled).toBe(false);
  });

  it("judges the whole form as it changes, before OK", async () => {
    await open();
    await listed();
    type("Overlay Mode", "mqtt");
    type("MQTT Host", "broker");
    await screen.findByRole("option", { name: "com.axis.scene.frame.v1#1" });
    type("Data Source", "com.axis.scene.frame.v1#1");
    expect(ok().disabled).toBe(false);
    type("MQTT Port", "0");
    expect(ok().disabled).toBe(true);
    expect(screen.getByRole("alert").textContent).toBe("MQTT Port must be between 1 and 65535.");
  });

  it("adds the data source or topic shown, which the camera's list has, and none else", async () => {
    await open();
    await listed();
    expect((screen.getByLabelText("DataHub Topic") as HTMLSelectElement).value).toBe(
      "com.axis.scene.frame.v1",
    );
    // A changed camera has not listed anything: the old topic is no longer offered or added.
    type("Host", "10.0.0.8");
    await waitFor(() => expect(ok().disabled).toBe(true));
    expect((screen.getByLabelText("DataHub Topic") as HTMLSelectElement).value).toBe("");
    expect(screen.queryByRole("option", { name: "com.axis.scene.frame.v1" })).toBeNull();
    expect(screen.getByRole("alert").textContent).toBe(
      "Topic is required for WebSocket overlay mode.",
    );
  });

  it("forgets the camera's lists when the connection changes, even back to an earlier one, and ignores a late answer", async () => {
    let release = () => {};
    gate = new Promise((resolve) => (release = resolve));
    const read = new Promise<void>((resolve) => (onRead = resolve));
    await open();
    await waitFor(() => expect(listings).toEqual(["datahub"]));
    type("Host", "10.0.0.8");
    type("Host", "");
    // The late answer is read and the form has handled it before anything is checked.
    await act(async () => {
      release();
      await read;
    });
    // The first answer was for a connection the form has left, and coming back does not ask again by itself.
    expect(screen.getByRole("option", { name: "Refresh to load DataHub topics" })).toBeTruthy();
    expect(listings).toEqual(["datahub"]);
    expect(ok().disabled).toBe(true);

    refresh("DataHub Topic");
    await listed();
    // Refreshing under the same connection keeps the topic.
    refresh("DataHub Topic");
    await screen.findByRole("option", { name: "Select DataHub topics" });
    expect((screen.getByLabelText("DataHub Topic") as HTMLSelectElement).value).toBe(
      "com.axis.scene.frame.v1",
    );
  });

  it("says a host is needed, not a failure, while the host reference names nothing", async () => {
    vi.stubGlobal("fetch", async (url: string) =>
      url === "/api/handlers"
        ? { ok: true, status: 200, json: async () => HANDLERS }
        : {
            ok: false,
            status: 422,
            json: async () => ({
              problem: "Invalid",
              error: {
                code: "WorkspaceInvalid",
                params: {},
                reason: "Enter the device host.",
                location: "item-1",
              },
            }),
          },
    );
    await open();
    type("Host", "$UNSET_HOST");
    // A changed connection is read again on choosing a mode, or on Refresh.
    type("Overlay Mode", "none");
    type("Overlay Mode", "datahub");
    await screen.findByRole("option", { name: "Enter a device host to load DataHub topics" });
    expect(screen.getByRole("alert").textContent).toBe("Enter the device host.");
    expect(screen.queryByText(/Could not load/)).toBeNull();
  });
});

it("keeps the overlay chosen before the decoders arrive", async () => {
  const before = fetch;
  vi.stubGlobal("fetch", async (url: string, init?: RequestInit) => {
    if (url === "/api/handlers") await new Promise((resolve) => setTimeout(resolve, 50));
    return before(url, init);
  });
  render(
    <FakeSettings state={DEFAULT_SETTINGS}>
      <SessionProvider>
        <AddLiveStreamDialog onClose={() => {}} />
      </SessionProvider>
    </FakeSettings>,
  );
  fireEvent.change(await screen.findByLabelText("Overlay Mode"), { target: { value: "none" } });
  await screen.findByRole("option", { name: "ADF v1 Frame" });
  expect((screen.getByLabelText("Overlay Mode") as HTMLSelectElement).value).toBe("none");
});

describe("placeholders of references", () => {
  const REFERENCES = {
    ...DEFAULT_SETTINGS,
    values: {
      ...DEFAULT_SETTINGS.values,
      "camera.host": "$AX_DEVIL_TARGET_ADDR",
      "camera.user": "$AX_DEVIL_TARGET_USER",
      "camera.password": "$AX_DEVIL_TARGET_PASS",
    },
  };
  const placeholder = (label: string) =>
    (screen.getByLabelText(label) as HTMLInputElement).placeholder;
  const show = async (answer: object) => {
    const asked = vi.fn();
    const before = fetch;
    vi.stubGlobal("fetch", async (url: string, init?: RequestInit) => {
      if (url !== "/api/live/expansions") return before(url, init);
      asked(init?.body);
      return { ok: true, status: 200, json: async () => answer };
    });
    render(
      <GrantsContext value={["preferences"]}>
        <FakeSettings state={REFERENCES}>
          <SessionProvider>
            <AddLiveStreamDialog onClose={() => {}} />
          </SessionProvider>
        </FakeSettings>
      </GrantsContext>,
    );
    return asked;
  };

  it("show the value and the variable it comes from, and a password only as dots", async () => {
    const asked = await show({
      host: "172.20.1.1",
      username: "root",
      password_set: true,
      broker_host: null,
      broker_user: null,
      broker_pass_set: null,
    });
    await waitFor(() =>
      expect(placeholder("Host")).toBe("172.20.1.1  (via $AX_DEVIL_TARGET_ADDR)"),
    );
    expect(placeholder("Username")).toBe("root  (via $AX_DEVIL_TARGET_USER)");
    expect(placeholder("Password")).toBe("••••••••  (via $AX_DEVIL_TARGET_PASS)");
    expect(placeholder("Name")).toBe("172.20.1.1");
    // What the backend may not expand stays the reference.
    expect(placeholder("MQTT Host")).not.toContain("via");
    expect(JSON.parse(asked.mock.calls[0]?.[0] as string)).toEqual({});
  });

  it("stay the reference where the backend does not expand it, and empty where the variable is not set", async () => {
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
});
