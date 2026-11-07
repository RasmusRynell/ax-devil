import {
  useEffect,
  useId,
  useLayoutEffect,
  useRef,
  useState,
  type CSSProperties,
  type KeyboardEvent,
  type ReactNode,
} from "react";
import { useOutsidePress } from "./useOutsidePress";

const FIRST_CONTROL =
  'input:not(:disabled), select:not(:disabled), button:not(:disabled), [role^="menuitem"]:not(:disabled)';

interface Props {
  /** The trigger's accessible name and tooltip, and the panel's name. */
  label: string;
  /** What the trigger shows: an icon, say, or a name. */
  trigger: ReactNode;
  /** The panel's content, or a function of the way to close it. */
  children: ReactNode | ((close: () => void) => ReactNode);
  /** White on the near-black video scrim, instead of the theme's text color. */
  onCanvas?: boolean;
  /** Sizes the trigger. */
  className?: string;
}

/** Keeps `[start, start + size]` inside `[from, to]`, preferring the start. */
function within(start: number, size: number, from: number, to: number): number {
  return Math.max(from, Math.min(start, to - size));
}

/**
 * Where a panel opens for its button: below it when it fits, else above it when there is more room there, kept inside
 * the nearest ancestor marked with `data-popover-bounds` (the window without one), and no larger than that area, so it
 * scrolls instead of leaving it. Its height is its content's, not the height it was last capped to, so a placement
 * made again for the same content comes out the same.
 */
function placement(button: HTMLElement, panel: HTMLElement): CSSProperties {
  const at = button.getBoundingClientRect();
  const area = button.closest("[data-popover-bounds]")?.getBoundingClientRect();
  const { left, top, right, bottom } = area ?? {
    left: 0,
    top: 0,
    right: window.innerWidth,
    bottom: window.innerHeight,
  };
  const width = Math.min(panel.offsetWidth, right - left);
  const natural = panel.scrollHeight + panel.offsetHeight - panel.clientHeight;
  const height = Math.min(natural, bottom - top);
  // Below the button; above it when it does not fit below and there is more room above.
  const above = height > bottom - at.bottom && at.top - top > bottom - at.bottom;
  const y = within(above ? at.top - height : at.bottom, height, top, bottom);
  return {
    left: within(at.left, width, left, right),
    top: y,
    maxWidth: `min(48ch, ${right - left}px)`,
    maxHeight: (above ? at.top : bottom) - y,
  };
}

/**
 * A button that opens a panel of controls beside it. Opening moves focus into the panel; Escape, a click elsewhere
 * or Tab out of it closes it (the click that closes it is not passed on), and Escape returns focus to the button. The panel keeps plain keys to itself, so
 * Space on one of its controls never reaches the viewer's shortcuts.
 */
export function Popover({ label, trigger, children, onCanvas = false, className = "" }: Props) {
  const [open, setOpen] = useState(false);
  const [place, setPlace] = useState<CSSProperties>();
  const root = useRef<HTMLDivElement>(null);
  const button = useRef<HTMLButtonElement>(null);
  const panel = useRef<HTMLDivElement>(null);
  const panelId = useId();
  const close = () => setOpen(false);

  // The panel is placed when it opens and again whenever its content changes its size (another tab, a scrollbar).
  useLayoutEffect(() => {
    if (!open) return setPlace(undefined);
    const [trigger, box] = [button.current, panel.current];
    if (!trigger || !box) return;
    const update = () => setPlace(placement(trigger, box));
    update();
    if (typeof ResizeObserver === "undefined") return;
    const watch = new ResizeObserver(update);
    watch.observe(box);
    return () => watch.disconnect();
  }, [open]);

  useOutsidePress(() => root.current, close, open);

  useEffect(() => {
    if (open) panel.current?.querySelector<HTMLElement>(FIRST_CONTROL)?.focus();
  }, [open]);

  const onKey = (event: KeyboardEvent) => {
    if (event.ctrlKey || event.altKey || event.metaKey || (event.key === "Escape" && !open)) return;
    event.stopPropagation();
    if (event.key !== "Escape") return;
    event.preventDefault();
    setOpen(false);
    button.current?.focus();
  };

  const tone = onCanvas ? "text-on-canvas hover:bg-canvas-hover" : "text-fg hover:bg-hover";
  return (
    <div
      ref={root}
      onKeyDown={onKey}
      onKeyUp={(event) => event.stopPropagation()}
      onBlur={(event) => {
        if (event.relatedTarget && !root.current?.contains(event.relatedTarget)) setOpen(false);
      }}
      className="relative min-w-0"
    >
      <button
        ref={button}
        type="button"
        aria-label={label}
        title={label}
        aria-haspopup="dialog"
        aria-expanded={open}
        aria-controls={open ? panelId : undefined}
        onClick={() => setOpen(!open)}
        className={`flex h-row max-w-full items-center gap-s rounded-control bg-transparent focus-ring-inset ${tone} ${className}`}
      >
        {trigger}
      </button>
      {open && (
        <div
          ref={panel}
          id={panelId}
          role="dialog"
          aria-label={label}
          style={place}
          className="fixed z-10 max-h-screen w-max max-w-[min(48ch,100vw)] overflow-y-auto rounded-popup border border-line-strong bg-popup p-m text-fg shadow-popup"
        >
          {typeof children === "function" ? children(close) : children}
        </div>
      )}
    </div>
  );
}
