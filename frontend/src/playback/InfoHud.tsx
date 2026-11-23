import { useCallback, useLayoutEffect, useRef, useState } from "react";
import type { LaneDisplay } from "../lanes/contract";
import { hudSections, linesFit, type HudSection } from "./hud";
import { basisOf } from "../lanes/lanes";
import type { PlaybackView } from "./view";

/**
 * Timing diagnostics over a lane's picture: how the frame on screen and the lane's overlay sample line up. It ignores
 * the pointer, a row too long for the picture is cut off on the right, and what does not fit the height ends in a row
 * of `…`.
 */
export function InfoHud({
  stream,
  view,
  lane,
}: {
  stream: Pick<LaneDisplay, "matched">;
  view: PlaybackView;
  /** The lane's position in `view.lanes`. */
  lane: number;
}) {
  const { shown, videoPts } = view;
  const state = view.lanes[lane];
  if (!shown) return null;
  const sections = hudSections(
    shown,
    videoPts,
    state.source === null
      ? null
      : {
          match: stream.matched(lane),
          pending: state.pending,
          settings: state.settings,
          basis: basisOf(state) ?? "Time",
        },
  );
  return <HudBox label={`${state.label}: frame info`} sections={sections} />;
}

/**
 * The sections in a box that takes the height the lane's column leaves it. Every line stays laid out, so the box is as
 * tall as that room whatever is cut; lines past it are invisible and the last one in sight reads `…`.
 */
function HudBox({ label, sections }: { label: string; sections: readonly HudSection[] }) {
  const box = useRef<HTMLElement>(null);
  const total = sections.reduce((sum, { rows }) => sum + 1 + rows.length, 0);
  const [fit, setFit] = useState(total);
  const measure = useCallback(() => {
    const el = box.current;
    if (!el) return;
    const { bottom } = el.getBoundingClientRect();
    const room = bottom - (parseFloat(getComputedStyle(el).paddingBottom) || 0);
    const lines = [...el.querySelectorAll<HTMLElement>("[data-line]")].map((line) => ({
      heading: line.dataset.line === "heading",
      bottom: line.getBoundingClientRect().bottom,
    }));
    setFit(linesFit(lines, room));
  }, []);
  // After every render, so a change of rows is measured before it is painted; the observer covers a change of room.
  useLayoutEffect(measure);
  useLayoutEffect(() => {
    const observer = new ResizeObserver(measure);
    if (box.current) observer.observe(box.current);
    return () => observer.disconnect();
  }, [measure]);
  const shown = Math.min(fit, total);
  let start = 0;
  return (
    <section
      ref={box}
      aria-label={label}
      className="pointer-events-none min-h-0 max-w-full overflow-hidden rounded-control bg-surface/50 p-m font-mono text-small text-fg select-none"
    >
      {sections.map(({ title, rows }, index) => {
        const first = start;
        start += 1 + rows.length;
        // Line `at` of the HUD: invisible past the fit, and `…` when it is the last in sight of a cut HUD.
        const line = (at: number, key: string, kind: string, text: string, className: string) => (
          <p
            key={key}
            data-line={kind}
            className={`m-0 truncate ${className} ${at >= shown ? "invisible" : ""}`}
          >
            {at === shown - 1 && shown < total ? "…" : text}
          </p>
        );
        return (
          <div key={title} className={index > 0 ? "mt-m" : undefined}>
            {line(
              first,
              title,
              "heading",
              title,
              "mb-s w-fit max-w-full border border-line-strong bg-panel px-m py-xs font-semibold",
            )}
            {rows.map(([name, value], row) =>
              line(first + 1 + row, name, "row", `${name}: ${value}`, ""),
            )}
          </div>
        );
      })}
    </section>
  );
}
