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

it("rounds the frame rate as Python's round(x, 2) with :g does, a tie to the even digit", () => {
  const shown = (fps: number) => videoDetails({ ...described, fps }).split(" · ")[1];
  // 25.125 and 0.125 are exact ties; 2.675 is a double just below its tie.
  expect([25.125, 30, 29.97, 23.976023976, 2.675, 0.125].map(shown)).toEqual([
    "25.12 fps",
    "30 fps",
    "29.97 fps",
    "23.98 fps",
    "2.67 fps",
    "0.12 fps",
  ]);
});
