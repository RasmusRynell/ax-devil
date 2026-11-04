import { describe, expect, it } from "vite-plus/test";
import type { ItemState, NewLiveWire } from "../generated/api";
import { DEFAULT_SETTINGS } from "../fakeSettings";
import { formFrom, newLive } from "../shell/liveForm";
import { camerasOf, seedOf } from "./cameras";

const stream = (id: string, label: string, recipe: NewLiveWire): ItemState => ({
  id,
  label,
  kind: "live",
  open: false,
  resolution: {
    status: "Resolved",
    content: {
      kind: "LiveVideo",
      id,
      generation: 1,
      display_name: label,
      slot: "live",
      overlay: "mqtt",
      lanes: [label],
      recipe,
    },
  },
});

describe("cameras of live streams", () => {
  it("groups streams by host whatever their labels, and a copy keeps the connection and nothing else", () => {
    const mqtt: NewLiveWire = {
      label: "Lobby",
      host: "10.0.0.2",
      username: "root",
      password: "$CAM_PASS",
      overlay: { mode: "mqtt", broker_host: "10.0.0.9", source_key: "scene" },
    };
    const items = [
      stream("a", "Lobby", mqtt),
      stream("b", "Door", {
        ...mqtt,
        label: "Door",
        host: " 10.0.0.2 ",
        overlay: { mode: "none" },
      }),
    ];
    const [camera, ...others] = camerasOf(items);
    expect(others).toEqual([]);
    expect(camera?.name).toBe("Lobby");
    expect(camera?.streams.map(({ id }) => id)).toEqual(["a", "b"]);

    const copy = newLive(formFrom(DEFAULT_SETTINGS.values, seedOf({ like: "a" }, items)));
    expect(copy).toMatchObject({
      host: "10.0.0.2",
      username: "root",
    });
    expect(copy.password).toBeUndefined();
    expect(copy.label).toBeUndefined();
    expect(copy.overlay).toEqual({ mode: "none" });
  });
});
