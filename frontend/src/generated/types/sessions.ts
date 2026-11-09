// Generated from sessions by make; do not edit.

/**
 * One seat's two cursor choices; both are on by default.
 */
export type CursorOptions = { 
/**
 * Receive other seats' cursors.
 */
show: boolean, 
/**
 * Broadcast this seat's cursor.
 */
share: boolean, };

/**
 * Why a Session's socket was told it is over.
 */
export type EndedReason = "stopped" | "ownerGone" | "replaced";

/**
 * Whose view a seat shows.
 */
export type SeatMode = "following" | "exploring";

/**
 * Which seat a participant holds.
 */
export type SeatRole = "owner" | "watcher";
