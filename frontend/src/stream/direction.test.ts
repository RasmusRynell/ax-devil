// @vitest-environment node
import { describe, expect, it } from "vite-plus/test";
import { WantPlanner } from "./direction";

const paused = (playhead: number) => ({ playhead, playing: false });

describe("window split", () => {
  it.each([
    { name: "playing", ahead: 85, behind: 15, steps: [], play: true, direction: "forward" },
    { name: "paused", ahead: 50, behind: 50, steps: [], play: false, direction: "forward" },
    {
      name: "after one backward step",
      ahead: 50,
      behind: 50,
      steps: [-1],
      play: false,
      direction: "forward",
    },
    {
      name: "in backward mode",
      ahead: 15,
      behind: 85,
      steps: [-1, -1],
      play: false,
      direction: "backward",
    },
    {
      name: "after a forward step",
      ahead: 50,
      behind: 50,
      steps: [-1, -1, 1],
      play: false,
      direction: "forward",
    },
    {
      name: "after a broken backward run",
      ahead: 50,
      behind: 50,
      steps: [-1, 1, -1],
      play: false,
      direction: "forward",
    },
    {
      name: "after play",
      ahead: 85,
      behind: 15,
      steps: [-1, -1],
      play: true,
      direction: "forward",
    },
  ])("is $ahead ahead and $behind behind $name", ({ ahead, behind, steps, play, direction }) => {
    const planner = new WantPlanner(101);
    steps.forEach((delta) => planner.stepped(delta));
    if (play) planner.played();
    const want = planner.want({ playhead: 500, playing: play });
    expect(want).toMatchObject({ ahead, behind, playhead: 500, direction });
  });

  it("keeps the window the size of the buffer", () => {
    const planner = new WantPlanner(166);
    for (const steps of [[], [-1, -1]]) {
      steps.forEach((delta) => planner.stepped(delta));
      const want = planner.want(paused(0));
      expect(want.ahead + want.behind + 1).toBe(166);
    }
  });
});
