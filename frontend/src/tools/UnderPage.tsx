import type { ReactNode } from "react";
import type { Steps } from "./pageStack";

/** The page drawn over a list, and the key of the row of the list it was opened from, if any. */
export type Open = { from: string | null; render: (steps: Steps) => ReactNode };

/** No page before or after. */
export const NO_STEPS: Steps = { previous: null, next: null };

/**
 * A list with the `open` page drawn over it. While a page is open the list stays as it is, out of reach under the page,
 * which is given the pages either side of its own in the list's current order.
 */
export function UnderPage({
  open,
  steps,
  children,
}: {
  open: Open | null;
  steps: Steps;
  children: ReactNode;
}) {
  return (
    <div className="relative flex min-h-0 flex-1 flex-col">
      <div inert={open !== null} className="flex min-h-0 flex-1 flex-col">
        {children}
      </div>
      {open && <div className="absolute inset-0 flex flex-col bg-panel">{open.render(steps)}</div>}
    </div>
  );
}
