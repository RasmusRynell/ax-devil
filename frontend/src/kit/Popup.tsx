import { useEffect, useLayoutEffect, useRef, type ReactNode, type RefObject } from "react";

const FIRST_ITEM = '[role^="menuitem"]:not(:disabled)';

interface Props {
  onClose: () => void;
  /** The element whose clicks do not count as outside, such as the button that opened this; defaults to the popup. */
  boundary?: RefObject<HTMLElement | null>;
  /** Fixes the popup at the pointer, kept inside the window; without it the caller positions it with `className`. */
  at?: { x: number; y: number };
  className?: string;
  children: ReactNode;
}

/**
 * A floating panel for a menu. It takes focus when it opens, and closes on Escape (returning focus to where it
 * was), on a click outside it, and when focus leaves it. Pointers inside it are never shared with a session.
 */
export function Popup({ onClose, boundary, at, className = "", children }: Props) {
  const panel = useRef<HTMLDivElement>(null);
  const close = useRef(onClose);
  useEffect(() => {
    close.current = onClose;
  });

  useLayoutEffect(() => {
    const element = panel.current;
    if (!element || !at) return;
    element.style.left = `${Math.max(0, Math.min(at.x, window.innerWidth - element.offsetWidth))}px`;
    element.style.top = `${Math.max(0, Math.min(at.y, window.innerHeight - element.offsetHeight))}px`;
  }, [at]);

  useEffect(() => {
    const opener = document.activeElement as HTMLElement | null;
    panel.current?.querySelector<HTMLElement>(FIRST_ITEM)?.focus();
    const outside = (event: PointerEvent) => {
      if (!(boundary?.current ?? panel.current)?.contains(event.target as Node)) close.current();
    };
    const escape = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      event.preventDefault();
      opener?.focus();
      close.current();
    };
    document.addEventListener("pointerdown", outside);
    // In the capture phase, so the Escape that closes a popup is spent before any handler around it sees it.
    document.addEventListener("keydown", escape, true);
    return () => {
      document.removeEventListener("pointerdown", outside);
      document.removeEventListener("keydown", escape, true);
    };
  }, [boundary]);

  return (
    <div
      ref={panel}
      data-cursor-region="popup"
      onBlur={(event) => {
        const next = event.relatedTarget as Node | null;
        if (next && !(boundary?.current ?? panel.current)?.contains(next)) close.current();
      }}
      className={`rounded-popup border border-line-strong bg-popup p-xs shadow-popup ${at ? "fixed" : ""} ${className}`}
    >
      {children}
    </div>
  );
}
