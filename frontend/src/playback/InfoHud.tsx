import { hudSections } from "./hud";
import { basisOf } from "./lanes";
import type { PlaybackSession } from "./session";
import type { PlaybackView } from "./view";

/**
 * Timing diagnostics over a lane's picture: how the frame on screen and the lane's overlay sample line up. It ignores
 * the pointer, a row too long for the picture is cut off on the right, and what does not fit the height is clipped.
 */
export function InfoHud({
  session,
  view,
  lane,
}: {
  session: PlaybackSession;
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
          match: session.matched(lane),
          pending: state.pending,
          settings: state.settings,
          basis: basisOf(state) ?? "Time",
        },
  );
  return (
    <section
      aria-label={`${state.label}: frame info`}
      className="pointer-events-none min-h-0 max-w-full overflow-hidden rounded-control bg-canvas-scrim px-m py-s font-mono text-small text-on-canvas select-none"
    >
      {sections.map(({ title, rows }) => (
        <div key={title}>
          <p className="m-0 truncate font-semibold">{title}</p>
          {rows.map(([label, value]) => (
            <p key={label} className="m-0 truncate">{`${label}: ${value}`}</p>
          ))}
        </div>
      ))}
    </section>
  );
}
