import type { AddItemsRequest, ApiError } from "../generated/api";

/**
 * Adds videos to the workspace and asks for a viewer on each. Resolves to why the backend refused them, else `null`;
 * the backend adds all of them or none.
 */
export async function addVideos(paths: readonly string[]): Promise<string | null> {
  const request: AddItemsRequest = {
    items: paths.map((path) => ({ type: "Video", path })),
    open: true,
  };
  const response = await fetch("/api/workspace/items", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(request),
  });
  if (response.ok) return null;
  const error = (await response.json().catch(() => null)) as ApiError | null;
  if (!error) return `the backend answered ${response.status}`;
  return error.location ? `${error.reason} (${error.location})` : error.reason;
}
