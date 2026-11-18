import { expect, it } from "vite-plus/test";
import {
  admit,
  preview,
  receive,
  settle,
  stamp,
  type Choice,
  type DeviceState,
} from "./deviceState";

it.each([
  "A: earlier own announcement",
  "B: equal persisted choice",
  "submission order",
  "reverse order",
  "first refused",
  "preview after prepare",
  "foreign preview",
  "foreign pending",
  "failed then foreign",
  "two blocked saves",
])("derived Device intents: %s", (scenario) => {
  let s: DeviceState = {
    raw: { "ui.theme": "auto" },
    local: new Map(),
    applied: new Map(),
    clock: 0,
  };
  const prepare = (value: string, key: Choice["key"] = "ui.theme") => {
    const [next, stamps] = stamp(s, 1);
    s = next;
    return [{ key, value, stamp: stamps[0]! }];
  };
  const commit = (choices: Choice[], failed = false) => {
    const [next, survivors] = admit(s, choices);
    s = next;
    if (!failed && survivors.length) {
      const values = { ...s.raw };
      for (const { key, value } of survivors) values[key] = value;
      s = receive(s, { values, foreign: [] });
    }
    s = settle(s, survivors, failed ? survivors.map((c) => c.key) : []);
  };
  const shown = () => s.local.get("ui.theme")?.value ?? s.raw["ui.theme"];
  if (scenario === "B: equal persisted choice") {
    s = preview(s, "ui.theme", "dark");
    commit(prepare("auto"));
    expect(shown()).toBe("auto");
    expect(s.local.size).toBe(0);
  } else if (scenario === "preview after prepare") {
    const a = prepare("light");
    s = preview(s, "ui.theme", "dark");
    commit(a);
    expect(shown()).toBe("dark");
    expect(s.raw["ui.theme"]).toBe("auto");
  } else if (scenario.startsWith("foreign")) {
    const a = prepare("light");
    if (scenario === "foreign pending") [s] = admit(s, a);
    else s = preview(s, "ui.theme", "light");
    s = receive(s, { values: { "ui.theme": "dark" }, foreign: ["ui.theme"] });
    expect(shown()).toBe(scenario === "foreign pending" ? "light" : "dark");
  } else if (scenario === "failed then foreign") {
    commit(prepare("light"), true);
    expect(s.local.get("ui.theme")?.state).toBe("unsaved");
    s = receive(s, { values: s.raw, foreign: [] });
    expect(shown()).toBe("light");
    s = receive(s, { values: { "ui.theme": "dark" }, foreign: ["ui.theme"] });
    expect(shown()).toBe("dark");
  } else if (scenario === "two blocked saves") {
    commit(prepare("light"), true);
    commit(prepare("large", "ui.text_size"), true);
    expect(s.local.size).toBe(2);
    expect(s.raw).toEqual({ "ui.theme": "auto" });
  } else {
    const a = prepare("light"),
      b = prepare("dark");
    if (scenario === "reverse order") {
      commit(b);
      commit(a);
    } else {
      commit(a, scenario === "first refused");
      commit(b);
    }
    expect(shown()).toBe("dark");
    expect(s.local.size).toBe(0);
  }
});
