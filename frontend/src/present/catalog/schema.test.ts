// @vitest-environment node
import { readFileSync, writeFileSync } from "node:fs";
import { expect, it } from "vite-plus/test";
import { catalogSchema } from "./schema";

const FILE = new URL("../../../../assets/catalogs/catalog.schema.json", import.meta.url);

it("assets/catalogs/catalog.schema.json is the schema the compiler's tables describe", () => {
  const fresh = `${JSON.stringify(catalogSchema(), null, 2)}\n`;
  if (process.env.UPDATE_GOLDENS) writeFileSync(FILE, fresh);
  expect(
    readFileSync(FILE, "utf8"),
    "stale: regenerate with UPDATE_GOLDENS=1 make tests-frontend TEST=schema",
  ).toBe(fresh);
});
