import { expect, it } from "vite-plus/test";
import { entriesOf, scenario } from "../session/fakeScenario";
import { hiddenOf } from "./hidden";
import { shownEntry } from "./shownEntry";

it("shows the named entry, else the first one not hidden in the whole item, as the backend does", () => {
  // Two playlists, P of A and B and Q of C and D, with all of P hidden.
  const groups = scenario("playlist-groups");
  const entries = entriesOf(groups.doc("two"));
  const hidden = hiddenOf(groups.doc("p-hidden").workspace.rows);
  // No entry named yet: the first one not hidden is in the second playlist, whichever the viewer would start in.
  expect(shownEntry(entries, hidden, undefined)?.label).toBe("C");
  // A named entry stays even hidden, as when the tree opened it; one the item does not have gives way.
  expect(shownEntry(entries, hidden, groups.ids["list/A"])?.label).toBe("A");
  expect(shownEntry(entries, hidden, "gone")?.label).toBe("C");
  // A hidden first entry of the second playlist is skipped too, for an absent and for an unknown entry.
  const abc = hiddenOf(groups.doc("abc-hidden").workspace.rows);
  expect(shownEntry(entries, abc, undefined)?.label).toBe("D");
  expect(shownEntry(entries, abc, "gone")?.label).toBe("D");
  // Every entry hidden: the first.
  const list = scenario("playlist");
  const all = hiddenOf(list.doc("abcd-hidden").workspace.rows);
  expect(shownEntry(entriesOf(list.doc()), all, undefined)?.label).toBe("A");
  expect(shownEntry([], hidden, undefined)).toBeUndefined();
});
