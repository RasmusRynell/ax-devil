import { expect, it } from "vite-plus/test";
import { videoDetails } from "./videoDetails";

const described = { width: 640, height: 480, fps: 25, frame_count: 100, duration_us: 4_000_000 };

it("tells size, frame rate and length, leaving out what the video does not declare", () => {
  expect(videoDetails(described)).toBe("640×480 · 25 fps · 0:04");
  expect(videoDetails({ ...described, fps: 29.97003, duration_us: 3_661_000_000 })).toBe(
    "640×480 · 29.97 fps · 1:01:01",
  );
  // A one-frame video has no length of its own: its frame lasts a period.
  expect(videoDetails({ ...described, frame_count: 1, duration_us: 0 })).toBe(
    "640×480 · 25 fps · 0:00",
  );
  expect(videoDetails({ ...described, fps: null })).toBe("640×480");
});
