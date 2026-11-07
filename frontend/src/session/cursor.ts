import type { CursorPoint, CursorRegion } from "../generated/api";

/** What each region of this window shows right now: the sidebar view id, or the open viewer's id or `welcome`. */
export type Scopes = Partial<Record<CursorRegion, string>>;

/** The longest element id or scope the wire carries. */
const NAME_MAX = 64;
const REGIONS: readonly CursorRegion[] = ["titleBar", "activityBar", "sidebar", "main"];

const clamp = (value: number) => Math.min(1, Math.max(0, value));
const sized = (rect: DOMRect) => rect.width > 0 && rect.height > 0;
const scopeOf = (scopes: Scopes, region: CursorRegion) => (scopes[region] ?? "").slice(0, NAME_MAX);

/**
 * The name an anchor goes by on the wire: its `data-cursor-id`, with the pane's number before it inside a pane
 * (`data-cursor-pane`), so two panes showing the same thing keep distinct anchors and a cursor lands in the right one.
 */
function nameOf(anchor: HTMLElement): string {
  const pane = anchor.parentElement?.closest<HTMLElement>("[data-cursor-pane]")?.dataset.cursorPane;
  return `${pane ? `${pane}/` : ""}${anchor.dataset.cursorId ?? ""}`;
}

/**
 * Where a pointer at the client position is: the offset inside the nearest `[data-cursor-id]` element, within the
 * nearest `[data-cursor-region]`, plus what that region shows. Null outside every region. An element without a size
 * or with an id over the wire limit is left out, so the receiver falls back to the region. A popup marks itself
 * `data-cursor-region="popup"`, so a pointer inside it is never sent.
 */
export function cursorPointAt(
  target: EventTarget | null,
  clientX: number,
  clientY: number,
  scopes: Scopes,
): CursorPoint | null {
  if (!(target instanceof Element)) return null;
  const regionElement = target.closest<HTMLElement>("[data-cursor-region]");
  const region = REGIONS.find((name) => name === regionElement?.dataset.cursorRegion);
  const box = regionElement?.getBoundingClientRect();
  if (!region || !box || !sized(box)) return null;
  const point: CursorPoint = {
    region,
    scope: scopeOf(scopes, region),
    x: clamp((clientX - box.left) / box.width),
    y: clamp((clientY - box.top) / box.height),
  };
  const anchor = target.closest<HTMLElement>("[data-cursor-id]");
  const rect = anchor?.getBoundingClientRect();
  const id = anchor ? nameOf(anchor) : "";
  if (rect && sized(rect) && id.length <= NAME_MAX)
    point.element = {
      id,
      x: clamp((clientX - rect.left) / rect.width),
      y: clamp((clientY - rect.top) / rect.height),
    };
  return point;
}

/**
 * Where a remote cursor is in this window, in viewport pixels, or null to hide it.
 *
 * With an element id the cursor goes inside that element, whatever the region shows; without one it goes by the
 * region fraction, and only when this window's region shows the same scope. Either way the cursor is hidden when the
 * target is missing or has no size, or when something else is on top of the placed point: the element found there
 * must be the target or inside it. That one rule covers a popup or dialog over it and a scroll container that has
 * clipped it out of view.
 */
export function placeCursor(
  point: CursorPoint,
  scopes: Scopes,
  root: Document,
): { x: number; y: number } | null {
  const { element } = point;
  let target: Element | undefined;
  if (element)
    target = [...root.querySelectorAll("[data-cursor-id]")].find(
      (candidate) => nameOf(candidate as HTMLElement) === element.id,
    );
  else if (point.scope === scopeOf(scopes, point.region))
    target = root.querySelector(`[data-cursor-region="${point.region}"]`) ?? undefined;
  const rect = target?.getBoundingClientRect();
  if (!target || !rect || !sized(rect)) return null;

  const { x: fx, y: fy } = element ?? point;
  const spot = { x: rect.left + clamp(fx) * rect.width, y: rect.top + clamp(fy) * rect.height };
  // The right and bottom edges belong to the next box, so test one pixel inside.
  const hit = root.elementFromPoint(
    Math.min(spot.x, rect.right - 1),
    Math.min(spot.y, rect.bottom - 1),
  );
  return hit && target.contains(hit) ? spot : null;
}
