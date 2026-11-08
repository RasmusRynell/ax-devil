import { expect, it } from "vite-plus/test";
import { entry } from "../session/fakeSession";
import type { Hidden } from "./hidden";
import { shownEntry } from "./shownEntry";

const hiding = (...ids: string[]): Hidden => ({
  entry: (id) => ids.includes(id),
  lane: () => false,
});

it("shows the named entry, else the first one not hidden in the whole item, as the backend does", () => {
  // Two playlists, the first with every entry hidden.
  const entries = [entry("a1"), entry("a2"), entry("b1"), entry("b2")];
  const hidden = hiding("a1", "a2", "b1");
  // No entry named yet: the first one not hidden is in the second playlist, whichever the viewer would start in.
  expect(shownEntry(entries, hidden, undefined)?.id).toBe("b2");
  // A named entry stays even hidden, as when the tree opened it; one the item does not have gives way.
  expect(shownEntry(entries, hidden, "a1")?.id).toBe("a1");
  expect(shownEntry(entries, hidden, "gone")?.id).toBe("b2");
  // Every entry hidden: the first.
  expect(shownEntry(entries, hiding("a1", "a2", "b1", "b2"), undefined)?.id).toBe("a1");
  expect(shownEntry([], hidden, undefined)).toBeUndefined();
});
