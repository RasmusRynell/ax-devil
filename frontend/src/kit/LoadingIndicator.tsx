import { MiddleElided } from "./MiddleElided";

/** The arc's circle in a 31-unit box: radius 14 plus the 3-unit stroke, a quarter of the circle open. */
const RADIUS = 14;
const ARC = 0.75 * 2 * Math.PI * RADIUS;

/**
 * A translucent cover over the positioned element it sits in, with a turning arc and, when there is one, a message
 * under it that loses its middle where it does not fit. It stands still for those who ask for less motion.
 */
export function LoadingIndicator({ message = "" }: { message?: string }) {
  return (
    <div
      role="status"
      aria-busy="true"
      aria-label="Loading"
      className="absolute inset-0 flex flex-col items-center justify-center gap-s bg-canvas-scrim px-m text-on-canvas-muted"
    >
      <svg viewBox="0 0 31 31" aria-hidden="true" className="size-spinner motion-safe:animate-spin">
        <circle
          cx="15.5"
          cy="15.5"
          r={RADIUS}
          fill="none"
          stroke="currentColor"
          strokeWidth="3"
          strokeLinecap="round"
          strokeDasharray={`${ARC} ${2 * Math.PI * RADIUS}`}
        />
      </svg>
      {message && <MiddleElided text={message} className="max-w-full" />}
    </div>
  );
}
