import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type MouseEvent,
  type PointerEvent,
} from "react";
import { useWindowMaximized } from "../windowBridge";

const isTitle = (target: EventTarget) =>
  target instanceof Element &&
  Boolean(target.closest('[data-cursor-id="title:drag"], [data-cursor-id="title:name"]'));

/** Linux's maximized-title restore gesture; normal windows keep Electron's native drag region. */
export function useTitleDrag() {
  const maximized = useWindowMaximized();
  const bridge = window.axDevilWindow;
  const pointer = useRef<{ id: number; element: HTMLElement } | null>(null);
  const [dragging, setDragging] = useState(false);
  const end = useCallback(() => {
    const held = pointer.current;
    if (!held) return;
    pointer.current = null;
    setDragging(false);
    bridge?.restoreDrag?.("end");
    if (held.element.hasPointerCapture(held.id)) held.element.releasePointerCapture(held.id);
  }, [bridge]);
  useEffect(() => {
    const escape = (event: KeyboardEvent) => {
      if (event.key === "Escape") end();
    };
    window.addEventListener("blur", end);
    window.addEventListener("keydown", escape, true);
    return () => {
      window.removeEventListener("blur", end);
      window.removeEventListener("keydown", escape, true);
      end();
    };
  }, [end]);
  return {
    manual: Boolean(bridge?.restoreDrag && (maximized || dragging)),
    onPointerDown: (event: PointerEvent<HTMLElement>) => {
      if (!maximized || !bridge?.restoreDrag || event.button !== 0) return;
      if (!isTitle(event.target)) return;
      event.preventDefault();
      end();
      pointer.current = { id: event.pointerId, element: event.currentTarget };
      setDragging(true);
      event.currentTarget.setPointerCapture(event.pointerId);
      bridge.restoreDrag("start", event.currentTarget.getBoundingClientRect().height);
    },
    onPointerMove: (event: PointerEvent<HTMLElement>) => {
      if (!pointer.current || pointer.current.id !== event.pointerId) return;
      if (!(event.buttons & 1)) end();
      else bridge?.restoreDrag?.("move");
    },
    onDoubleClick: (event: MouseEvent<HTMLElement>) => {
      if (isTitle(event.target)) bridge?.toggleMaximize();
    },
    onPointerUp: end,
    onPointerCancel: end,
    onLostPointerCapture: end,
  };
}
