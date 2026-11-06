import { describe, expect, it } from "vite-plus/test";
import { DEFAULT_SETTINGS } from "../fakeSettings";
import { configuredOverlay, formValues, startForm } from "./liveForm";

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
});
