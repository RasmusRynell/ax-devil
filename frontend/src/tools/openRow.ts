import type { FilePage } from "../inspect/fileScope";
import type { ListState } from "./listState";

/**
 * Where the object of the open page is in File scope's list, for the rows around it to be asked for. `row` is its row
 * as the newest page tells it: from the backend's `located` when the page was asked about the object, else from the rows
 * loaded; `null` when the list does not hold it or the page says nothing. `near` is `row`, or where the panel last
 * had the object while the page says nothing, which the list's order may since have moved.
 */
export function openRow(
  entity: string | null,
  page: FilePage | null,
  anchor: ListState["anchor"],
): { row: number | null; near: number | null } {
  if (entity === null) return { row: null, near: null };
  if (page?.located?.id === entity) return { row: page.located.row, near: page.located.row };
  const at = page?.rows.findIndex((row) => row.id === entity) ?? -1;
  const row = page && at >= 0 ? page.offset + at : null;
  return { row, near: row ?? (anchor?.entity === entity ? anchor.row : null) };
}
