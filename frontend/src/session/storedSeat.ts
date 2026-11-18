/** The tab's seat (its session and resume token), so a reload within the grace period takes it back. Another tab has its own. */
export const STORED_SEAT = "ax-devil.seat";
export interface StoredSeat {
  session: string;
  token: string;
  /** Whether it is the tab's own Session rather than one it watches. */
  own: boolean;
}

/** The seat the tab stored, if any. */
export function readStoredSeat(): StoredSeat | null {
  const stored = window.sessionStorage.getItem(STORED_SEAT);
  return stored ? (JSON.parse(stored) as StoredSeat) : null;
}
