import type { MouseEvent } from "react";

/** The characters of a cut text that always show. */
const TAIL = 5;

/** On hover, gives the element `text` as its tooltip while it, or a child of it, is cut short, and none otherwise. */
export const tipIfCut = (text: string) => (event: MouseEvent<HTMLElement>) => {
  const element = event.currentTarget;
  const cut = [element, ...Array.from(element.children)].some(
    (part) => part.scrollWidth > part.clientWidth,
  );
  element.title = cut ? text : "";
};

/** One line of text cut in the middle where it does not fit, its last `tail` characters kept; the cut one has the whole as its tooltip. */
export function MiddleCut({
  text,
  tail = TAIL,
  className,
}: {
  text: string;
  tail?: number;
  className?: string;
}) {
  return (
    <div onMouseEnter={tipIfCut(text)} className={`flex min-w-0 ${className ?? ""}`}>
      <span className="truncate">{text.length > 2 * tail ? text.slice(0, -tail) : text}</span>
      {text.length > 2 * tail && <span className="shrink-0">{text.slice(-tail)}</span>}
    </div>
  );
}
