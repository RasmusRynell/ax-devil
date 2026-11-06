/** Whether a drag carries files from outside the page; the app's own drags (panes, tabs, rows) do not. */
const carriesFiles = (event: Event) =>
  (event as DragEvent).dataTransfer?.types.includes("Files") === true;

/**
 * [AI] Keeps a file dragged from the desktop from opening in the page, which a browser tab does by leaving the app.
 * Dropping files on the window does nothing (AC-02 is dropped). Returns the way to stop.
 */
export function ignoreFileDrops(target: Document = document): () => void {
  const ignore = (event: Event) => {
    if (carriesFiles(event)) event.preventDefault();
  };
  target.addEventListener("dragover", ignore);
  target.addEventListener("drop", ignore);
  return () => {
    target.removeEventListener("dragover", ignore);
    target.removeEventListener("drop", ignore);
  };
}
