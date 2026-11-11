import { ask } from "../resolvers/handlers";
import { reasonOf } from "../errors";
import { compileCatalog } from "../present";
import type { FreshCatalog } from "../generated/api";

/** Rereads the saved document; the action owner reconciles and compiles it before committing. */
export async function readFreshCatalog(id: string): Promise<FreshCatalog> {
  const answer = await ask<FreshCatalog>("/api/catalogs/reload", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ id }),
  });
  if (!answer.ok) throw new Error(reasonOf(answer.refusal));
  return answer.value;
}

/** Compiles a fresh document for a default change, whose persistence checks its revision again. */
export async function freshCatalog(id: string): Promise<number> {
  const fresh = await readFreshCatalog(id);
  const result = compileCatalog(fresh.document);
  if (!result.ok) throw new Error(result.diagnostics.map((d) => d.message).join("\n"));
  return fresh.revision;
}
