import { useEffect } from "react";

/** What a workspace without a name is called. */
export const UNTITLED = "Untitled";

/** The window's title: the workspace's name and the app, with a dot in front while it has unsaved changes. */
export function windowTitle(workspace: { name: string | null; modified: boolean }): string {
  return `${workspace.modified ? "● " : ""}${workspace.name ?? UNTITLED} — ax-devil`;
}

/** Keeps the document's title, which the desktop window and a browser tab follow, on the workspace's. */
export function useWindowTitle(workspace: { name: string | null; modified: boolean } | undefined) {
  const title = workspace ? windowTitle(workspace) : "ax-devil";
  useEffect(() => {
    document.title = title;
  }, [title]);
}
