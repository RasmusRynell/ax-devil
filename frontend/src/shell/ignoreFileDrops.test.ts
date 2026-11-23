import { afterEach, describe, expect, it } from "vite-plus/test";
import { ignoreFileDrops } from "./ignoreFileDrops";

const stop = ignoreFileDrops();
afterEach(stop);

/** A drag event carrying `types`, as the browser reports what is being dragged. */
const drag = (type: string, types: string[]) => {
  const event = new Event(type, { bubbles: true, cancelable: true });
  Object.defineProperty(event, "dataTransfer", { value: { types } });
  document.body.dispatchEvent(event);
  return event;
};

describe("ignoreFileDrops", () => {
  it("cancels a drag of files so the page is not left, and leaves the app's own drags alone", () => {
    expect(drag("dragover", ["Files"]).defaultPrevented).toBe(true);
    expect(drag("drop", ["Files"]).defaultPrevented).toBe(true);
    expect(drag("dragover", ["application/x-pane"]).defaultPrevented).toBe(false);
    expect(drag("drop", ["text/plain"]).defaultPrevented).toBe(false);
  });
});
