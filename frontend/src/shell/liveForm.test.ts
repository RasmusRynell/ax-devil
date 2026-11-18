import { describe, expect, it } from "vite-plus/test";
import { DEFAULT_SETTINGS } from "../client/fakeSettings";
import {
  configuredOverlay,
  formFrom,
  formValues,
  headOf,
  hiddenSecrets,
  newLive,
  startForm,
} from "./liveForm";

const DECODERS = [
  {
    handler: "ADF_V1_FRAME",
    display_name: "ADF v1 Frame",
    extensions: [],
    options: [],
    live: true,
  },
];

describe("the Add Live Stream form's defaults", () => {
  it("start from what a $NAME setting gives, but never for the host or a login", () => {
    const settings = {
      ...DEFAULT_SETTINGS,
      values: {
        ...DEFAULT_SETTINGS.values,
        "camera.head": "$HEAD",
        "camera.overlay": "$MODE",
        "datahub.handler": "$DECODER",
        "datahub.channel": "$CHANNEL",
      },
      resolved: {
        "camera.head": 2,
        "camera.overlay": "datahub",
        "datahub.handler": "ADF_V1_FRAME",
        "datahub.channel": 7,
        "camera.host": "10.0.0.7",
      },
    } as const;
    const values = formValues(settings);
    expect(startForm(values)).toMatchObject({ head: "2", channel: "7" });
    expect(configuredOverlay(values, DECODERS)).toEqual({
      overlay: "datahub",
      handler: "ADF_V1_FRAME",
    });
    expect(values["camera.host"]).toBe("$AX_DEVIL_TARGET_ADDR");
  });

  it("start from the default, not the reference, when the backend gives no value for it", () => {
    const values = formValues({
      ...DEFAULT_SETTINGS,
      values: { ...DEFAULT_SETTINGS.values, "camera.head": "$HEAD", "broker.port": "$PORT" },
    });
    expect(startForm(values)).toMatchObject({ head: "1", broker_port: "1883" });
  });

  it("list the sizes of the head the stream will use: the typed one, else the configured one, else 1", () => {
    const form = startForm(formValues(DEFAULT_SETTINGS));
    expect(headOf({ ...form, head: "3" }, "2")).toBe(3);
    expect(headOf({ ...form, head: "" }, "2")).toBe(2);
    expect(headOf({ ...form, head: "" }, "")).toBe(1);
  });
});

describe("the live form of an edited stream", () => {
  const values = formValues(DEFAULT_SETTINGS);
  const saved = formFrom(values, {
    host: "10.0.0.7",
    username: "",
    password: "",
    overlay: { mode: "rtsp", handler: "ADF_V1_FRAME" },
    kept_mqtt: { broker_host: "b.local", broker_port: 1884, broker_user: "mqtt", source_key: "k" },
    kept_datahub: { topic: "t", channel: 3 },
  });

  it("sends a cleared login as empty and the other modes' fields as kept, where an addition trims and drops blanks", () => {
    expect(newLive({ ...saved, username: " root " }, true)).toMatchObject({
      username: " root ",
      password: "",
      kept_mqtt: {
        broker_host: "b.local",
        broker_port: 1884,
        broker_user: "mqtt",
        source_key: "k",
      },
      kept_datahub: { topic: "t", channel: 3 },
    });
    expect(newLive({ ...saved, username: " root " })).toMatchObject({
      username: "root",
      password: undefined,
    });
  });

  it("shows what the stream saved as it is, empty and 0 included, and marks a hidden literal password", () => {
    // As the backend serializes a literal password: the key is missing.
    const wire = JSON.parse(
      `{"host":"10.0.0.7","username":"root","overlay":{"mode":"none"},
        "kept_mqtt":{"broker_host":"","broker_port":0,"broker_user":"","source_key":""},
        "kept_datahub":{"topic":"","channel":0}}`,
    );
    expect(formFrom(values, wire)).toMatchObject({
      broker_port: "0",
      source_key: "",
      topic: "",
      channel: "0",
    });
    expect(hiddenSecrets(wire)).toEqual(["password", "broker_pass"]);
    expect(hiddenSecrets({ ...wire, password: "" })).toEqual(["broker_pass"]);
  });

  it("sends the route only when chosen", () => {
    expect(newLive(saved).rtsp_route).toBeUndefined();
    expect(newLive({ ...saved, rtsp_route: "web_api" }).rtsp_route).toBe("web_api");
    expect(formFrom(values, { rtsp_route: "web_api" }).rtsp_route).toBe("web_api");
  });
});
