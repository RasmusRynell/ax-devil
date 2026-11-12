import { ask } from "../ask";
import { compileCatalog } from "../present";
import type { ErrorWire, FreshCatalog } from "../generated/api";

/** Rereads the saved document; the action owner reconciles and compiles it before committing. */
export async function readFreshCatalog(id: string): Promise<FreshCatalog> {
  const answer = await ask<FreshCatalog>("/api/catalogs/reload", {
    json: { id },
  });
  if (!answer.ok) throw answer.error;
  return answer.value;
}

/** Compiles a fresh document for a default change, whose persistence checks its revision again. */
export async function freshCatalog(id: string): Promise<number> {
  const fresh = await readFreshCatalog(id);
  const result = compileCatalog(fresh.document);
  if (!result.ok)
    throw {
      code: "CatalogInvalid",
      params: {},
      reason: result.diagnostics.map((d) => d.message).join("\n"),
    } satisfies ErrorWire;
  return fresh.revision;
}
