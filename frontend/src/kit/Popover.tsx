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

/** Keeps `[start, start + size]` inside `[0, limit]`, preferring the start. */
function within(start: number, size: number, limit: number): number {
  return Math.max(0, Math.min(start, limit - size));
}

/**
 * A button that opens a panel of controls beside it. Opening moves focus into the panel; Escape, a click elsewhere
 * or Tab out of it closes it, and Escape returns focus to the button. The panel keeps plain keys to itself, so
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

  useLayoutEffect(() => {
    if (!open || !button.current || !panel.current) return;
    const at = button.current.getBoundingClientRect();
    const { offsetWidth, offsetHeight } = panel.current;
    setPlace({
      left: within(at.left, offsetWidth, window.innerWidth),
      top: within(at.bottom, offsetHeight, window.innerHeight),
    });
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const outside = (event: PointerEvent) => {
      if (!root.current?.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener("pointerdown", outside);
    panel.current?.querySelector<HTMLElement>(FIRST_CONTROL)?.focus();
    return () => document.removeEventListener("pointerdown", outside);
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
          className="fixed z-10 max-h-[calc(100vh-var(--spacing-m)*2)] w-max max-w-[min(48ch,100vw)] overflow-y-auto rounded-popup border border-line-strong bg-popup p-m text-fg shadow-popup"
        >
          {typeof children === "function" ? children(close) : children}
        </div>
      )}
    </div>
  );
}
