import { describe, expect, it } from "vite-plus/test";
import { scenario } from "../session/fakeScenario";
import { hiddenOf } from "./hidden";

describe("hidden", () => {
  it("reads hidden entries and lanes from the rows", () => {
    // A playlist whose entry B and lane DPM of entry A are hidden.
    const { ids } = scenario("tree");
    const hidden = hiddenOf(scenario("tree").doc().workspace.rows);
    expect([ids["mot/A"], ids["mot/B"]].map((id) => hidden.entry(id))).toEqual([false, true]);
    expect(hidden.lane(ids["mot/A"], "DPM")).toBe(true);
    expect(hidden.lane(ids["mot/A"], "GT")).toBe(false);
    expect(hidden.lane(ids["mot/B"], "GT")).toBe(false);
    // A lane hidden in one entry is not hidden in another entry of the same label.
    const gt = hiddenOf(scenario("tree").doc("gt-hidden").workspace.rows);
    expect([ids["mot/A"], ids["mot/B"]].map((id) => gt.lane(id, "GT"))).toEqual([true, false]);
  });
});
