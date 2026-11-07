import { useEffect, useRef, useState } from "react";
import type { NetworkSnapshot } from "../generated/api";
import type { ScanProblem } from "../live/network";

/** One press of Rescan: which press it is, the connection it was made on, and its scan once the backend answers. */
type Press = { attempt: number; epoch: number; scan: number | null };

/**
 * Keeps the footer from starting a scan while one it started has not yet run. A press holds the guard from the press
 * until the snapshot stream shows a snapshot of the scan the backend answered with, or a later one; an older snapshot
 * cannot be mistaken for it. The press ends sooner when the backend refuses it, and when the stream reconnects,
 * because a restarted backend numbers its scans again. While the backend has not answered, any snapshot is older.
 * The answer of a press that a later one replaced, or that outlived the view, changes nothing. `scanning` is also
 * true while the snapshot itself says a scan runs.
 */
export function useScanGuard(snapshot: NetworkSnapshot | null, epoch: number) {
  const [held, setHeld] = useState<Press | null>(null);
  const latest = useRef(0);
  // Leaving the view makes every outstanding press stale.
  useEffect(() => {
    return () => {
      latest.current += 1;
    };
  }, []);
  if (
    held !== null &&
    (held.epoch !== epoch || (held.scan !== null && (snapshot?.scan ?? -1) >= held.scan))
  )
    setHeld(null);

  /**
   * Starts a scan with `start`, which resolves to the scan's number or why it was refused. `settled` hears the
   * refusal, else `null`, unless a later press has replaced this one.
   */
  const press = (
    start: () => Promise<number | ScanProblem>,
    settled: (problem: ScanProblem | null) => void,
  ) => {
    const attempt = ++latest.current;
    setHeld({ attempt, epoch, scan: null });
    void start().then((answer) => {
      if (attempt !== latest.current) return;
      // A press that the reconnect already ended stays ended.
      const answered = typeof answer === "number";
      settled(answered ? null : answer);
      setHeld((now) =>
        now?.attempt !== attempt ? now : answered ? { ...now, scan: answer } : null,
      );
    });
  };
  return { scanning: snapshot?.scanning === true || held !== null, press };
}
