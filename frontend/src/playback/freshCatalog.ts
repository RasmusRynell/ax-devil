import { ask } from "../resolvers/handlers";
import { reasonOf } from "../errors";
import { compileCatalog } from "../present";
import type { FreshCatalog } from "../generated/api";

/** Rereads and compiles the saved document before an action can persist or propagate it. */
export async function freshCatalog(
  id: string,
  received?: (fresh: FreshCatalog) => void,
): Promise<number> {
  const answer = await ask<FreshCatalog>("/api/catalogs/reload", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ id }),
  });
  if (!answer.ok) throw new Error(reasonOf(answer.refusal));
  received?.(answer.value);
  const result = compileCatalog(answer.value.document);
  if (!result.ok) throw new Error(result.diagnostics.map((d) => d.message).join("\n"));
  return answer.value.revision;
}
