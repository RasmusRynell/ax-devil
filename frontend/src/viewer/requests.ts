import type { ApiError, CatalogsInfo, HiddenRequest } from "../generated/api";

/** Why the backend refused, in words for the user. */
async function refusal(response: Response): Promise<string> {
  const error = (await response.json().catch(() => null)) as ApiError | null;
  if (!error) return `the backend answered ${response.status}`;
  return error.location ? `${error.reason} (${error.location})` : error.reason;
}

/** The catalogs a lane can be drawn with, and the one a new lane starts with. */
export async function fetchCatalogs(signal?: AbortSignal): Promise<CatalogsInfo> {
  const response = await fetch("/api/catalogs", { signal });
  if (!response.ok) throw new Error(await refusal(response));
  return (await response.json()) as CatalogsInfo;
}

/** Hides or shows an entry, or one lane of it. Resolves to why the backend refused, else `null`. */
export async function setHidden(
  entry: string,
  lane: string | null,
  hidden: boolean,
): Promise<string | null> {
  const request: HiddenRequest = { target: { entry, lane }, hidden };
  const response = await fetch("/api/workspace/hidden", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(request),
  });
  return response.ok ? null : refusal(response);
}
