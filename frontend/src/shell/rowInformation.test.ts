import { expect, it } from "vite-plus/test";
import { scenario } from "../session/fakeScenario";
import { rowInformation } from "./rowInformation";
import { treeOf } from "./treeModel";

it("lists a playlist's own fields in order and an entry's overlay with its file and handler", () => {
  const { workspace } = scenario("tree-info").doc();
  const { items, rows } = workspace;
  const tree = treeOf(workspace);
  const information = (kind: string) =>
    rowInformation(
      tree.find((row) => row.info.kind === kind)!,
      items,
      rows,
    )!;

  const list = information("playlist");
  expect(list.fields.map(([label]) => label)).toEqual([
    "type",
    "entries",
    "lanes",
    "matches",
    "resolver",
    "owner",
  ]);
  expect(list.fields.at(-1)).toEqual(["owner", "lab"]);
  expect(information("entry").fields.at(-1)).toEqual(["split", "train"]);

  const [lane] = information("entry").children;
  expect(lane.fields).toEqual(
    expect.arrayContaining([
      ["video", "SEQ-01"],
      ["path", "/data/runs/GT.txt"],
      ["handler type", "MOT_FILE"],
      ["detector", "Yes"],
    ]),
  );
});
