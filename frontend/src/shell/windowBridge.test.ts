import { describe, expect, it } from "vite-plus/test";
import { installFakeFullscreen } from "../fakeFullscreen";
import { toggleAppFullscreen } from "./windowBridge";

const fake = installFakeFullscreen();
const settled = () => new Promise((done) => setTimeout(done));

describe("application fullscreen in a browser", () => {
  it("makes the page fullscreen even from a lane's fullscreen, and leaves it again", async () => {
    const lane = document.body.appendChild(document.createElement("div"));
    await lane.requestFullscreen();
    toggleAppFullscreen();
    await settled();
    expect(fake.element).toBe(document.documentElement);
    toggleAppFullscreen();
    await settled();
    expect(fake.element).toBeNull();
    lane.remove();
  });
});
