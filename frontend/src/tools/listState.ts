import type { Sort } from "./objects";

/** Which objects the list shows: those of the frame on screen or those of the whole file. */
export type Scope = "frame" | "file";

/** What each scope is called and explains itself as, what it starts sorted by, and what an empty list says. */
export const SCOPES: Record<Scope, { title: string; hint: string; sort: Sort; none: string }> = {
  frame: {
    title: "Frame",
    hint: "Objects in the displayed frame, searchable by any attribute",
    sort: { key: "score", descending: true },
    none: "No objects in this frame",
  },
  file: {
    title: "File",
    hint: "Every object in the file; objects not in the displayed frame are dimmed",
    sort: { key: "id", descending: false },
    none: "No objects in the file",
  },
};

/** What the user set on the objects list: kept by the viewer, so a page opened from the list steps through the same one after the panel closes and opens. */
export type ListState = {
  scope: Scope;
  text: string;
  sorts: Record<Scope, Sort>;
  /** Where File scope last found the object of the open page, so the rows around it load again once the panel opens. */
  anchor: { entity: string; row: number } | null;
};

/** A list nobody has searched or sorted. */
export const FRESH_LIST: ListState = {
  scope: "frame",
  text: "",
  sorts: { frame: SCOPES.frame.sort, file: SCOPES.file.sort },
  anchor: null,
};
