import { describe, expect, it } from "vite-plus/test";
import { DEFAULT_SETTINGS } from "../fakeSettings";
import { scenario } from "../session/fakeScenario";
import { formFrom, newLive } from "./liveForm";
import { camerasOf, seedOf } from "../live/cameras";

const live = scenario("cameras");

describe("cameras of live streams", () => {
  // Known bug: the backend writes shown.host padded (src/session/project.rs:287 via crates/workspace/src/live.rs:318,
  // host.rs:49 returns `typed` as is), and camerasOf trims the recipe's host (cameras.ts:125) but keys on shown.host (:127).
  it.fails("groups a stream whose host is padded with the one of the same host", () => {
    const [camera, ...others] = camerasOf(live.doc("padded").workspace.items);
    expect(others).toEqual([]);
    expect(camera?.name).toBe("Lobby");
    expect(camera?.streams.map(({ id }) => id)).toEqual([live.ids.a, live.ids.b]);
  });

  it("groups streams by host whatever their labels, and a copy keeps the connection and nothing else", () => {
    const items = live.doc().workspace.items;
    const [camera, ...others] = camerasOf(items);
    expect(others).toEqual([]);
    expect(camera?.name).toBe("Lobby");
    expect(camera?.streams.map(({ id }) => id)).toEqual([live.ids.a, live.ids.b]);

    const copy = newLive(formFrom(DEFAULT_SETTINGS.values, seedOf({ like: live.ids.a }, items)));
    expect(copy).toMatchObject({
      host: "10.0.0.2",
      username: "root",
      rtsp_route: "web_api",
    });
    expect(copy.password).toBeUndefined();
    expect(copy.label).toBeUndefined();
    expect(copy.overlay).toEqual({ mode: "none" });
  });
});
