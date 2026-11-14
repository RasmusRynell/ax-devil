import { apiFetch, failWith } from "../client/api";
import { reasonOf } from "../client/errors";
import { linesOf } from "../client/lines";
import type { TaskLine, TaskProgressInfo } from "../generated/api";
import type { TaskRun } from "./bus";

/** Runs a task over an entry through the backend until it is done, failed or aborted, telling `onProgress` as it goes. */
export async function streamTask(
  { item, entry }: { item: string | null; entry: string | null },
  { handler, lanes, options }: TaskRun,
  onProgress: (progress: TaskProgressInfo) => void,
  signal: AbortSignal,
): Promise<void> {
  const response = await apiFetch(`/api/tasks/${encodeURIComponent(handler)}/run`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ item, entry, lanes, options }),
    signal,
  });
  if (!response.ok) return failWith(response);
  for await (const line of linesOf(response)) {
    const message = JSON.parse(line) as TaskLine;
    if ("progress" in message) onProgress(message.progress);
    else if ("error" in message) throw new Error(reasonOf(message.error));
    else return;
  }
  throw new Error("The task ended without saying it was done");
}
