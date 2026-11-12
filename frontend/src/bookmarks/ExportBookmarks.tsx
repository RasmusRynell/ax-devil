import { splitPath } from "../../../shared/file-name.mjs";
import { useCommand, useDoc } from "../session/session";
import { exportFile, usePickPath } from "../client/pickPath";

/**
 * Exporting every bookmark to a CSV file on the machine that runs the backend: the function that starts it, which asks
 * where to write the file. A failure goes to `onFail`.
 */
export function useExportBookmarks(onFail: (error: unknown) => void): () => void {
  const command = useCommand();
  const pick = usePickPath();
  const workspace = useDoc()?.workspace;
  const name = `${workspace?.name ?? "Untitled"} bookmarks.csv`;
  const current = workspace?.file;
  const folder = current ? splitPath(current).folder : undefined;
  return () =>
    void pick?.(exportFile(name, "csv", folder)).then(
      (path) =>
        path
          ? command({ type: "workspace", command: { type: "exportBookmarks", path } }).catch(onFail)
          : undefined,
      onFail,
    );
}
